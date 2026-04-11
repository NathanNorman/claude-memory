#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for code_chunker.py — Java and Kotlin chunking."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import code_chunker


JAVA_SOURCE = """\
package com.example;

import java.util.List;

public class ManifestFinder {
    private final String basePath;

    public ManifestFinder(String basePath) {
        this.basePath = basePath;
    }

    public List<String> findManifests() {
        // implementation
        return List.of();
    }

    public boolean isManifest(String path) {
        return path.contains("manifest") && path.endsWith(".json");
    }
}
"""

KOTLIN_SOURCE = """\
package com.example

import org.junit.Test

class QueryManifestValidationIT {
    @Test
    fun testAllQueryManifestValidators() {
        // test implementation
        val validators = listOf("v1", "v2")
        validators.forEach { assert(it.isNotEmpty()) }
    }

    @Test
    fun testQueryManifestSchemaVersion() {
        val version = "2.0"
        assert(version == "2.0")
    }
}
"""


class TestJavaChunking(unittest.TestCase):
    """Test 1.5: Java chunking produces class chunks."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            suffix='.java', mode='w', delete=False, encoding='utf-8'
        )
        self.tmp.write(JAVA_SOURCE)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        Path(self.path).unlink(missing_ok=True)

    def test_produces_chunks(self):
        chunks = code_chunker.chunk_java_file(self.path)
        self.assertGreater(len(chunks), 0, "Should produce at least one chunk")

    def test_class_chunk_title_contains_class_name(self):
        chunks = code_chunker.chunk_java_file(self.path)
        titles = [c['title'] for c in chunks]
        matching = [t for t in titles if 'class ManifestFinder' in t]
        self.assertTrue(
            matching,
            f"Expected a chunk titled 'class ManifestFinder', got titles: {titles}",
        )

    def test_class_chunk_contains_methods(self):
        chunks = code_chunker.chunk_java_file(self.path)
        class_chunk = next(
            (c for c in chunks if 'class ManifestFinder' in c['title']), None
        )
        self.assertIsNotNone(class_chunk, "class ManifestFinder chunk must exist")
        self.assertIn('findManifests', class_chunk['content'])
        self.assertIn('isManifest', class_chunk['content'])

    def test_chunk_has_required_keys(self):
        chunks = code_chunker.chunk_java_file(self.path)
        for chunk in chunks:
            for key in ('title', 'content', 'start_line', 'end_line'):
                self.assertIn(key, chunk, f"Chunk missing key '{key}': {chunk}")

    def test_line_numbers_are_valid(self):
        chunks = code_chunker.chunk_java_file(self.path)
        for chunk in chunks:
            self.assertGreaterEqual(chunk['start_line'], 1)
            self.assertGreaterEqual(chunk['end_line'], chunk['start_line'])


class TestKotlinChunking(unittest.TestCase):
    """Test 1.6: Kotlin chunking produces function chunks."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            suffix='.kt', mode='w', delete=False, encoding='utf-8'
        )
        self.tmp.write(KOTLIN_SOURCE)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        Path(self.path).unlink(missing_ok=True)

    def test_produces_chunks(self):
        chunks = code_chunker.chunk_kotlin_file(self.path)
        self.assertGreater(len(chunks), 0, "Should produce at least one chunk")

    def test_class_chunk_title_contains_class_name(self):
        chunks = code_chunker.chunk_kotlin_file(self.path)
        titles = [c['title'] for c in chunks]
        matching = [t for t in titles if 'class QueryManifestValidationIT' in t]
        self.assertTrue(
            matching,
            f"Expected a chunk titled 'class QueryManifestValidationIT', got: {titles}",
        )

    def test_class_chunk_contains_both_functions(self):
        """The class chunk should contain both fun declarations in its content."""
        chunks = code_chunker.chunk_kotlin_file(self.path)
        class_chunk = next(
            (c for c in chunks if 'class QueryManifestValidationIT' in c['title']),
            None,
        )
        self.assertIsNotNone(class_chunk, "class QueryManifestValidationIT chunk must exist")
        self.assertIn('testAllQueryManifestValidators', class_chunk['content'])
        self.assertIn('testQueryManifestSchemaVersion', class_chunk['content'])

    def test_function_names_appear_in_some_chunk(self):
        """Each fun name should appear in at least one chunk's title or content."""
        chunks = code_chunker.chunk_kotlin_file(self.path)
        all_titles = ' '.join(c['title'] for c in chunks)
        all_content = ' '.join(c['content'] for c in chunks)
        combined = all_titles + ' ' + all_content

        self.assertIn(
            'testAllQueryManifestValidators', combined,
            "testAllQueryManifestValidators not found in any chunk",
        )
        self.assertIn(
            'testQueryManifestSchemaVersion', combined,
            "testQueryManifestSchemaVersion not found in any chunk",
        )

    def test_chunk_has_required_keys(self):
        chunks = code_chunker.chunk_kotlin_file(self.path)
        for chunk in chunks:
            for key in ('title', 'content', 'start_line', 'end_line'):
                self.assertIn(key, chunk, f"Chunk missing key '{key}': {chunk}")

    def test_line_numbers_are_valid(self):
        chunks = code_chunker.chunk_kotlin_file(self.path)
        for chunk in chunks:
            self.assertGreaterEqual(chunk['start_line'], 1)
            self.assertGreaterEqual(chunk['end_line'], chunk['start_line'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
