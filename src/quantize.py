"""
TurboQuant-style vector quantization for unified-memory.

Random rotation (block-diagonal Walsh-Hadamard + sign flips) followed by
Lloyd-Max scalar quantization. Zero overhead per vector — rotation seed
and codebook are shared across all vectors.

Reference: TurboQuant (ICLR 2026, arXiv:2504.19874)
"""

import math
import struct
from typing import Callable, Tuple

import numpy as np


# ──────────────────────────────────────────────────────────────
# Walsh-Hadamard Transform (block-diagonal for arbitrary dims)
# ──────────────────────────────────────────────────────────────


def _wht_inplace(x: np.ndarray) -> None:
    """In-place unnormalized Walsh-Hadamard transform. len(x) must be 2^k."""
    n = len(x)
    h = 1
    while h < n:
        # Vectorized butterfly: process all pairs at each level
        idx = np.arange(0, n, h * 2)
        for j in range(h):
            a = x[idx + j].copy()
            b = x[idx + h + j].copy()
            x[idx + j] = a + b
            x[idx + h + j] = a - b
        h *= 2


def _decompose_powers_of_2(n: int) -> list[int]:
    """Decompose n into distinct powers of 2 (binary representation)."""
    powers = []
    bit = 1
    while bit <= n:
        if n & bit:
            powers.append(bit)
        bit <<= 1
    return sorted(powers, reverse=True)


def generate_rotation(dims: int, seed: int) -> Tuple[Callable, Callable]:
    """Generate rotation via block-diagonal WHT + random sign flips.

    Decomposes dims into powers of 2 (e.g., 384 = 256 + 128), applies
    independent WHT+signs to each block. Result is an exact orthogonal
    transform using O(d) storage and O(d log d) compute.

    Returns:
        (forward_rotate, inverse_rotate) — each takes np.ndarray(d,) → np.ndarray(d,)
    """
    blocks = _decompose_powers_of_2(dims)
    rng = np.random.RandomState(seed)

    # Pre-generate random ±1 signs for each block
    block_signs = []
    offset = 0
    block_ranges = []
    for size in blocks:
        signs = rng.choice([-1.0, 1.0], size=size).astype(np.float32)
        block_signs.append(signs)
        block_ranges.append((offset, offset + size))
        offset += size

    def forward(x: np.ndarray) -> np.ndarray:
        y = x.astype(np.float32).copy()
        for (start, end), signs, size in zip(block_ranges, block_signs, blocks):
            block = y[start:end]
            block *= signs  # Apply random sign flips
            _wht_inplace(block)
            block /= math.sqrt(size)  # Normalize
            y[start:end] = block
        return y

    def inverse(y: np.ndarray) -> np.ndarray:
        x = y.astype(np.float32).copy()
        for (start, end), signs, size in zip(block_ranges, block_signs, blocks):
            block = x[start:end]
            _wht_inplace(block)
            block /= math.sqrt(size)  # WHT is self-inverse up to 1/n
            block *= signs  # Undo sign flips (signs are ±1, so self-inverse)
            x[start:end] = block
        return x

    return forward, inverse


# ──────────────────────────────────────────────────────────────
# Lloyd-Max Codebook (Gaussian approximation for sphere marginals)
# ──────────────────────────────────────────────────────────────

# After rotation, each coordinate of a unit vector on S^{d-1} follows
# approximately N(0, 1/d). This is exact in the limit d→∞ and an
# excellent approximation for d ≥ 100.


def _normal_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _lloyd_max_gaussian(num_levels: int, max_iter: int = 100) -> np.ndarray:
    """Compute Lloyd-Max optimal centroids for standard normal N(0,1).

    Uses the closed-form conditional expectation:
        E[X | a < X < b] = (φ(a) - φ(b)) / (Φ(b) - Φ(a))
    where φ = PDF, Φ = CDF.
    """
    # Initialize centroids: equally spaced in [-3, 3]
    centroids = np.linspace(-3.0, 3.0, num_levels)

    for _ in range(max_iter):
        # Decision boundaries = midpoints between adjacent centroids
        boundaries = np.empty(num_levels + 1)
        boundaries[0] = -np.inf
        boundaries[-1] = np.inf
        for i in range(1, num_levels):
            boundaries[i] = 0.5 * (centroids[i - 1] + centroids[i])

        # Update centroids: conditional expectation within each bin
        new_centroids = np.empty(num_levels)
        for i in range(num_levels):
            a, b = boundaries[i], boundaries[i + 1]
            # E[X | a < X < b] = (φ(a) - φ(b)) / (Φ(b) - Φ(a))
            pdf_a = _normal_pdf(a) if np.isfinite(a) else 0.0
            pdf_b = _normal_pdf(b) if np.isfinite(b) else 0.0
            cdf_a = _normal_cdf(a) if np.isfinite(a) else 0.0
            cdf_b = _normal_cdf(b) if np.isfinite(b) else 1.0
            denom = cdf_b - cdf_a
            if denom < 1e-15:
                new_centroids[i] = centroids[i]
            else:
                new_centroids[i] = (pdf_a - pdf_b) / denom

        if np.max(np.abs(new_centroids - centroids)) < 1e-10:
            break
        centroids = new_centroids

    return np.sort(centroids).astype(np.float32)


def compute_codebook(dims: int, bit_width: int) -> np.ndarray:
    """Compute Lloyd-Max codebook for the sphere coordinate distribution.

    After orthogonal rotation, each coordinate of a unit vector on S^{d-1}
    follows approximately N(0, 1/d). We compute optimal centroids for N(0,1)
    and scale by σ = 1/√d.

    Args:
        dims: Embedding dimensionality
        bit_width: Quantization bits per coordinate (1-8)

    Returns:
        Array of 2^bit_width centroids (sorted, float32)
    """
    if bit_width < 1 or bit_width > 8:
        raise ValueError(f'bit_width must be 1-8, got {bit_width}')

    num_levels = 1 << bit_width
    sigma = 1.0 / math.sqrt(dims)

    # Get optimal centroids for standard normal, then scale
    centroids = _lloyd_max_gaussian(num_levels)
    return (centroids * sigma).astype(np.float32)


# ──────────────────────────────────────────────────────────────
# Quantize / Dequantize
# ──────────────────────────────────────────────────────────────


def quantize(
    vector: np.ndarray,
    rotate_fn: Callable,
    codebook: np.ndarray,
) -> bytes:
    """Quantize a float32 vector to packed byte array.

    1. Rotate: y = Π · x
    2. For each coordinate, find nearest centroid index
    3. Pack indices into bytes (4-bit default: 2 indices per byte)

    Args:
        vector: float32 array of shape (d,)
        rotate_fn: Forward rotation function
        codebook: Sorted array of 2^b centroids

    Returns:
        Packed byte array of quantized indices
    """
    rotated = rotate_fn(vector)
    bit_width = int(math.log2(len(codebook)))

    # Vectorized nearest-centroid lookup
    # For each coordinate, find the index of the nearest centroid
    # Shape: (d, 1) vs (1, num_levels) -> (d, num_levels)
    diffs = np.abs(rotated.reshape(-1, 1) - codebook.reshape(1, -1))
    indices = np.argmin(diffs, axis=1).astype(np.uint8)

    return _pack_indices(indices, bit_width)


def dequantize(
    packed: bytes,
    inv_rotate_fn: Callable,
    codebook: np.ndarray,
    dims: int,
) -> np.ndarray:
    """Dequantize packed bytes back to approximate float32 vector.

    1. Unpack centroid indices
    2. Look up centroid values
    3. Inverse-rotate: x̂ = Π^T · ŷ

    Returns:
        Approximate float32 array of shape (d,)
    """
    bit_width = int(math.log2(len(codebook)))
    indices = _unpack_indices(packed, dims, bit_width)
    reconstructed = codebook[indices]
    return inv_rotate_fn(reconstructed)


def quantized_dot_product(
    query_rotated: np.ndarray,
    packed: bytes,
    codebook: np.ndarray,
    dims: int,
) -> float:
    """Compute approximate dot product in rotated space.

    For query q and stored vector v:
        q · v ≈ (Πq) · (codebook[indices])
        = Σ_j query_rotated[j] * codebook[index_j]

    This avoids full dequantization — just a lookup and multiply per dim.

    Args:
        query_rotated: Pre-rotated query vector (Π · q), shape (d,)
        packed: Packed quantized indices of stored vector
        codebook: Sorted centroid array
        dims: Original embedding dimensionality

    Returns:
        Approximate dot product (scalar)
    """
    bit_width = int(math.log2(len(codebook)))
    indices = _unpack_indices(packed, dims, bit_width)
    centroid_values = codebook[indices]
    return float(np.dot(query_rotated, centroid_values))


def batch_quantized_dot_products(
    query_rotated: np.ndarray,
    packed_list: list[bytes],
    codebook: np.ndarray,
    dims: int,
) -> np.ndarray:
    """Compute dot products of a query against multiple quantized vectors.

    Vectorized version of quantized_dot_product for batch search.

    Returns:
        Array of dot product scores, shape (N,)
    """
    bit_width = int(math.log2(len(codebook)))
    n = len(packed_list)

    # Unpack all vectors into a (N, d) matrix of centroid values
    matrix = np.empty((n, dims), dtype=np.float32)
    for i, packed in enumerate(packed_list):
        indices = _unpack_indices(packed, dims, bit_width)
        matrix[i] = codebook[indices]

    # Single matrix-vector multiply
    return matrix @ query_rotated


# ──────────────────────────────────────────────────────────────
# Search with reranking (for recall@10 ≥ 0.95)
# ──────────────────────────────────────────────────────────────


def search_with_rerank(
    query_vec: np.ndarray,
    packed_list: list[bytes],
    float32_matrix: np.ndarray,
    rotate_fn: Callable,
    codebook: np.ndarray,
    dims: int,
    top_k: int = 10,
    rerank_k: int = 30,
) -> list[tuple[int, float]]:
    """Two-stage search: quantized shortlist → exact reranking.

    Stage 1: Approximate dot products via quantized vectors (all N vectors)
    Stage 2: Exact dot products on the pre-normalized float32 matrix (top rerank_k only)

    With rerank_k=30 and top_k=10, achieves recall@10 ≥ 0.998 on real data.

    Args:
        query_vec: Original (unrotated) query vector, normalized
        packed_list: List of packed quantized vectors
        float32_matrix: Pre-normalized float32 matrix (N × d) for reranking
        rotate_fn: Forward rotation function
        codebook: Sorted centroid array
        dims: Embedding dimensionality
        top_k: Number of final results
        rerank_k: Number of candidates to rerank (must be ≥ top_k)

    Returns:
        List of (index, similarity_score) tuples, sorted by score descending
    """
    query_rotated = rotate_fn(query_vec)

    # Stage 1: quantized approximate search over all vectors
    approx_sims = batch_quantized_dot_products(
        query_rotated, packed_list, codebook, dims
    )
    candidate_indices = np.argsort(approx_sims)[-rerank_k:]

    # Stage 2: exact dot product on candidates only
    candidate_vecs = float32_matrix[candidate_indices]
    exact_sims = candidate_vecs @ query_vec

    # Sort candidates by exact similarity, take top_k
    top_within = np.argsort(exact_sims)[-top_k:][::-1]
    results = [
        (int(candidate_indices[j]), float(exact_sims[j]))
        for j in top_within
    ]
    return results


# ──────────────────────────────────────────────────────────────
# Bit packing / unpacking
# ──────────────────────────────────────────────────────────────


def _pack_indices(indices: np.ndarray, bit_width: int) -> bytes:
    """Pack quantization indices into a compact byte array.

    For bit_width=4: two indices per byte (high nibble, low nibble).
    For other widths: general bit packing.
    """
    if bit_width == 4:
        return _pack_4bit(indices)
    elif bit_width == 8:
        return indices.tobytes()
    elif bit_width == 2:
        return _pack_2bit(indices)
    elif bit_width == 1:
        return np.packbits(indices.astype(np.uint8)).tobytes()
    else:
        return _pack_general(indices, bit_width)


def _unpack_indices(packed: bytes, dims: int, bit_width: int) -> np.ndarray:
    """Unpack a byte array into quantization indices."""
    if bit_width == 4:
        return _unpack_4bit(packed, dims)
    elif bit_width == 8:
        return np.frombuffer(packed, dtype=np.uint8)[:dims]
    elif bit_width == 2:
        return _unpack_2bit(packed, dims)
    elif bit_width == 1:
        bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))
        return bits[:dims]
    else:
        return _unpack_general(packed, dims, bit_width)


def _pack_4bit(indices: np.ndarray) -> bytes:
    """Pack 4-bit indices: two per byte."""
    d = len(indices)
    n_bytes = (d + 1) // 2
    packed = bytearray(n_bytes)
    for i in range(0, d - 1, 2):
        packed[i // 2] = (int(indices[i]) << 4) | int(indices[i + 1])
    if d % 2 == 1:
        packed[-1] = int(indices[-1]) << 4
    return bytes(packed)


def _unpack_4bit(packed: bytes, dims: int) -> np.ndarray:
    """Unpack 4-bit indices from bytes."""
    indices = np.empty(dims, dtype=np.uint8)
    for i in range(0, dims - 1, 2):
        byte = packed[i // 2]
        indices[i] = (byte >> 4) & 0x0F
        indices[i + 1] = byte & 0x0F
    if dims % 2 == 1:
        indices[-1] = (packed[-1] >> 4) & 0x0F
    return indices


def _pack_2bit(indices: np.ndarray) -> bytes:
    """Pack 2-bit indices: four per byte."""
    d = len(indices)
    n_bytes = (d + 3) // 4
    packed = bytearray(n_bytes)
    for i in range(d):
        byte_idx = i // 4
        shift = 6 - 2 * (i % 4)
        packed[byte_idx] |= (int(indices[i]) & 0x03) << shift
    return bytes(packed)


def _unpack_2bit(packed: bytes, dims: int) -> np.ndarray:
    """Unpack 2-bit indices from bytes."""
    indices = np.empty(dims, dtype=np.uint8)
    for i in range(dims):
        byte_idx = i // 4
        shift = 6 - 2 * (i % 4)
        indices[i] = (packed[byte_idx] >> shift) & 0x03
    return indices


def _pack_general(indices: np.ndarray, bit_width: int) -> bytes:
    """General bit packing for arbitrary bit widths."""
    d = len(indices)
    total_bits = d * bit_width
    n_bytes = (total_bits + 7) // 8
    packed = bytearray(n_bytes)
    bit_pos = 0
    for i in range(d):
        val = int(indices[i])
        for b in range(bit_width - 1, -1, -1):
            byte_idx = bit_pos // 8
            bit_idx = 7 - (bit_pos % 8)
            if val & (1 << b):
                packed[byte_idx] |= (1 << bit_idx)
            bit_pos += 1
    return bytes(packed)


def _unpack_general(packed: bytes, dims: int, bit_width: int) -> np.ndarray:
    """General bit unpacking for arbitrary bit widths."""
    indices = np.empty(dims, dtype=np.uint8)
    bit_pos = 0
    for i in range(dims):
        val = 0
        for b in range(bit_width - 1, -1, -1):
            byte_idx = bit_pos // 8
            bit_idx = 7 - (bit_pos % 8)
            if packed[byte_idx] & (1 << bit_idx):
                val |= (1 << b)
            bit_pos += 1
        indices[i] = val
    return indices


# ──────────────────────────────────────────────────────────────
# Convenience: packed size calculation
# ──────────────────────────────────────────────────────────────


def packed_size(dims: int, bit_width: int) -> int:
    """Return the byte size of a packed quantized vector."""
    return (dims * bit_width + 7) // 8


# ──────────────────────────────────────────────────────────────
# Binary (1-bit) quantization — sign-based, for Hamming coarse pass
# ──────────────────────────────────────────────────────────────

# Pre-computed popcount lookup table: number of set bits for each byte value 0-255
_POPCOUNT_TABLE = np.array([bin(i).count('1') for i in range(256)], dtype=np.int32)


def quantize_binary(vectors: np.ndarray) -> np.ndarray:
    """Quantize float32 vectors to packed binary (1-bit per dimension).

    Sign-based thresholding at zero: each dimension becomes 1 if positive,
    0 if non-positive. Result is packed via np.packbits (big-endian).

    Args:
        vectors: float32 array of shape (N, d) or (d,).
            If 1-D, treated as a single vector and reshaped to (1, d).

    Returns:
        uint8 array of shape (N, ceil(d/8)) — packed binary vectors.
        For 768-d input, each row is 96 bytes.
        For 384-d input, each row is 48 bytes.
    """
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    return np.packbits(vectors > 0, axis=1)


def hamming_distance(binary_query: np.ndarray, binary_matrix: np.ndarray) -> np.ndarray:
    """Compute Hamming distances between a packed binary query and a matrix.

    Uses bitwise XOR followed by popcount via lookup table.

    Args:
        binary_query: uint8 array of shape (1, packed_dims) — single packed query
        binary_matrix: uint8 array of shape (N, packed_dims) — packed binary matrix

    Returns:
        int32 array of shape (N,) — Hamming distance (number of differing bits)
        between the query and each row in the matrix.
    """
    xor = np.bitwise_xor(binary_query, binary_matrix)
    return _POPCOUNT_TABLE[xor].sum(axis=1)
