#!/usr/bin/env python3
"""
Tests for binary (1-bit) quantization functions.

Tests:
1. Single vector quantization shape and sign preservation
2. Batch vector quantization
3. Hamming distance: identical vectors = 0
4. Hamming distance: known distance values
5. Round-trip sign preservation
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import numpy as np
from quantize import quantize_binary, hamming_distance


def test_single_vector_768d():
    """Single 768-d vector produces (1, 96) uint8 output."""
    vec = np.random.randn(768).astype(np.float32)
    result = quantize_binary(vec)
    assert result.shape == (1, 96), f'Expected (1, 96), got {result.shape}'
    assert result.dtype == np.uint8
    print('PASS: single vector 768d')


def test_single_vector_384d():
    """Single 384-d vector produces (1, 48) uint8 output."""
    vec = np.random.randn(384).astype(np.float32)
    result = quantize_binary(vec)
    assert result.shape == (1, 48), f'Expected (1, 48), got {result.shape}'
    print('PASS: single vector 384d')


def test_batch_vectors():
    """Batch of N vectors produces (N, packed_dims) output."""
    vecs = np.random.randn(100, 768).astype(np.float32)
    result = quantize_binary(vecs)
    assert result.shape == (100, 96), f'Expected (100, 96), got {result.shape}'
    print('PASS: batch vectors')


def test_sign_preservation():
    """Binary quantization preserves signs: positive dims -> 1, non-positive -> 0."""
    vec = np.array([1.0, -1.0, 0.5, -0.5, 0.0, 2.0, -3.0, 0.1], dtype=np.float32)
    result = quantize_binary(vec)
    # Expected bits: 1, 0, 1, 0, 0, 1, 0, 1 -> packed byte = 0b10100101 = 0xA5 = 165
    assert result.shape == (1, 1), f'Expected (1, 1), got {result.shape}'
    assert result[0, 0] == 0b10100101, f'Expected 165 (0xA5), got {result[0, 0]}'
    print('PASS: sign preservation')


def test_hamming_identical():
    """Identical vectors have Hamming distance 0."""
    vecs = np.random.randn(10, 768).astype(np.float32)
    binary = quantize_binary(vecs)
    for i in range(10):
        query = binary[i:i+1]
        dists = hamming_distance(query, binary)
        assert dists[i] == 0, f'Expected 0 for identical vector, got {dists[i]}'
    print('PASS: hamming identical = 0')


def test_hamming_known_distance():
    """Known distance: all-positive vs all-negative = max distance."""
    pos = np.ones((1, 16), dtype=np.float32)
    neg = -np.ones((1, 16), dtype=np.float32)
    b_pos = quantize_binary(pos)
    b_neg = quantize_binary(neg)
    dist = hamming_distance(b_pos, b_neg)
    assert dist[0] == 16, f'Expected 16, got {dist[0]}'
    print('PASS: hamming known distance')


def test_hamming_one_bit_diff():
    """Vectors differing in exactly one dimension have Hamming distance 1."""
    vec1 = np.ones(768, dtype=np.float32)
    vec2 = vec1.copy()
    vec2[0] = -1.0  # Flip one dimension
    b1 = quantize_binary(vec1)
    b2 = quantize_binary(vec2)
    dist = hamming_distance(b1, b2)
    assert dist[0] == 1, f'Expected 1, got {dist[0]}'
    print('PASS: hamming one bit diff')


def test_hamming_batch():
    """Batch Hamming distance returns correct shape."""
    vecs = np.random.randn(500, 768).astype(np.float32)
    binary = quantize_binary(vecs)
    query = binary[0:1]
    dists = hamming_distance(query, binary)
    assert dists.shape == (500,), f'Expected (500,), got {dists.shape}'
    assert dists[0] == 0  # Self-distance
    print('PASS: hamming batch shape')


if __name__ == '__main__':
    np.random.seed(42)
    test_single_vector_768d()
    test_single_vector_384d()
    test_batch_vectors()
    test_sign_preservation()
    test_hamming_identical()
    test_hamming_known_distance()
    test_hamming_one_bit_diff()
    test_hamming_batch()
    print('\nAll binary quantization tests passed.')
