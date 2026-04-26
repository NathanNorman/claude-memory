#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for SCIP parser — compiler-grade code intelligence edges."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from scip_parser import (
    detect_scip_languages,
    _parse_scip_json,
    _classify_scip_symbol,
    merge_scip_edges,
)


class TestDetectScipLanguages(unittest.TestCase):
    """Test language detection from build files."""

    def test_java_from_gradle_kts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'build.gradle.kts').touch()
            detected = detect_scip_languages(Path(tmpdir))
            self.assertIn('java', detected)

    def test_java_from_gradle_groovy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'build.gradle').touch()
            detected = detect_scip_languages(Path(tmpdir))
            self.assertIn('java', detected)

    def test_java_from_pom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'pom.xml').touch()
            detected = detect_scip_languages(Path(tmpdir))
            self.assertIn('java', detected)

    def test_typescript_from_tsconfig(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'tsconfig.json').touch()
            detected = detect_scip_languages(Path(tmpdir))
            self.assertIn('typescript', detected)

    def test_python_from_pyproject(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'pyproject.toml').touch()
            detected = detect_scip_languages(Path(tmpdir))
            self.assertIn('python', detected)

    def test_multi_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'build.gradle.kts').touch()
            (Path(tmpdir) / 'tsconfig.json').touch()
            detected = detect_scip_languages(Path(tmpdir))
            self.assertIn('java', detected)
            self.assertIn('typescript', detected)

    def test_no_build_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detected = detect_scip_languages(Path(tmpdir))
            self.assertEqual(detected, [])


class TestParseScipJson(unittest.TestCase):
    """Test SCIP JSON output parsing into edges."""

    def _make_scip_json(self, documents: list[dict]) -> str:
        return json.dumps({'documents': documents})

    def test_definition_and_reference_produces_edge(self):
        scip_json = self._make_scip_json([
            {
                'relativePath': 'src/Service.java',
                'occurrences': [
                    {'symbol': 'pkg.Service#doWork().', 'symbolRoles': 1},  # Definition
                ],
            },
            {
                'relativePath': 'src/Controller.java',
                'occurrences': [
                    {'symbol': 'pkg.Service#doWork().', 'symbolRoles': 0},  # Reference
                ],
            },
        ])
        edges = _parse_scip_json(scip_json)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]['source_file'], 'src/Controller.java')
        self.assertEqual(edges[0]['target_file'], 'src/Service.java')
        self.assertEqual(edges[0]['confidence'], 0.95)

    def test_same_file_reference_excluded(self):
        scip_json = self._make_scip_json([
            {
                'relativePath': 'src/Service.java',
                'occurrences': [
                    {'symbol': 'pkg.Service#doWork().', 'symbolRoles': 1},
                    {'symbol': 'pkg.Service#doWork().', 'symbolRoles': 0},  # Same file ref
                ],
            },
        ])
        edges = _parse_scip_json(scip_json)
        self.assertEqual(len(edges), 0)

    def test_method_symbol_classified_as_calls(self):
        result = _classify_scip_symbol('pkg.Service#doWork().')
        self.assertEqual(result, 'calls')

    def test_extends_classification(self):
        result = _classify_scip_symbol('pkg.Service#Interface')
        self.assertEqual(result, 'extends')

    def test_import_classification(self):
        result = _classify_scip_symbol('pkg.utils.StringHelper')
        self.assertEqual(result, 'import')

    def test_invalid_json(self):
        edges = _parse_scip_json('not json at all')
        self.assertEqual(edges, [])

    def test_metadata_contains_source_scip(self):
        scip_json = self._make_scip_json([
            {
                'relativePath': 'src/A.java',
                'occurrences': [
                    {'symbol': 'pkg.Foo#bar().', 'symbolRoles': 1},
                ],
            },
            {
                'relativePath': 'src/B.java',
                'occurrences': [
                    {'symbol': 'pkg.Foo#bar().', 'symbolRoles': 0},
                ],
            },
        ])
        edges = _parse_scip_json(scip_json)
        self.assertEqual(len(edges), 1)
        meta = json.loads(edges[0]['metadata'])
        self.assertEqual(meta['source'], 'scip')


class TestMergeScipEdges(unittest.TestCase):
    """Test merging SCIP edges with existing tree-sitter edges."""

    def test_scip_replaces_existing(self):
        existing = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'import', 'confidence': 0.5},
        ]
        scip = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'calls', 'confidence': 0.95},
        ]
        merged = merge_scip_edges(existing, scip)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['edge_type'], 'calls')
        self.assertEqual(merged[0]['confidence'], 0.95)

    def test_scip_only_added(self):
        existing = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'import'},
        ]
        scip = [
            {'source_file': 'c.java', 'target_file': 'd.java', 'edge_type': 'calls', 'confidence': 0.95},
        ]
        merged = merge_scip_edges(existing, scip)
        self.assertEqual(len(merged), 2)
        files = {(e['source_file'], e['target_file']) for e in merged}
        self.assertIn(('a.java', 'b.java'), files)
        self.assertIn(('c.java', 'd.java'), files)

    def test_existing_preserved_when_no_match(self):
        existing = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'import'},
            {'source_file': 'x.java', 'target_file': 'y.java', 'edge_type': 'extends'},
        ]
        scip = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'calls', 'confidence': 0.95},
        ]
        merged = merge_scip_edges(existing, scip)
        self.assertEqual(len(merged), 2)
        # x->y should still be 'extends' (untouched)
        xy = [e for e in merged if e['source_file'] == 'x.java'][0]
        self.assertEqual(xy['edge_type'], 'extends')

    def test_empty_scip_returns_existing(self):
        existing = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'import'},
        ]
        merged = merge_scip_edges(existing, [])
        self.assertEqual(len(merged), 1)

    def test_empty_existing_returns_scip(self):
        scip = [
            {'source_file': 'a.java', 'target_file': 'b.java', 'edge_type': 'calls', 'confidence': 0.95},
        ]
        merged = merge_scip_edges([], scip)
        self.assertEqual(len(merged), 1)


if __name__ == '__main__':
    unittest.main()
