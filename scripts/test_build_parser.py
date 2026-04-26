#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for build_parser.py — build file dependency parsing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from build_parser import (
    parse_gradle_kts,
    parse_gradle_groovy,
    parse_maven_pom,
    parse_package_json,
    parse_pyproject_toml,
    parse_requirements_txt,
    parse_settings_gradle,
    parse_version_catalog,
    resolve_catalog_refs,
)


class TestGradleKts(unittest.TestCase):
    def test_implementation_dep(self):
        content = 'implementation("com.google.guava:guava:31.1")'
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['group'], 'com.google.guava')
        self.assertEqual(deps[0]['artifact'], 'guava')
        self.assertEqual(deps[0]['version'], '31.1')
        self.assertFalse(deps[0]['is_internal'])

    def test_api_dep(self):
        content = 'api("org.slf4j:slf4j-api:2.0.0")'
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['scope'], 'api')

    def test_test_implementation(self):
        content = 'testImplementation("org.junit:junit:5.9.0")'
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['scope'], 'testImplementation')

    def test_project_dep(self):
        content = 'implementation(project(":core:common"))'
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0]['is_internal'])
        self.assertEqual(deps[0]['module_path'], ':core:common')
        self.assertEqual(deps[0]['coordinate'], 'project::core:common')

    def test_multiple_deps(self):
        content = '''
dependencies {
    implementation("com.google.guava:guava:31.1")
    api("org.slf4j:slf4j-api:2.0.0")
    testImplementation("org.junit:junit:5.9.0")
    implementation(project(":shared:utils"))
}
'''
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 4)

    def test_kapt_dep(self):
        content = 'kapt("com.google.dagger:dagger-compiler:2.44")'
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['scope'], 'kapt')

    def test_no_version(self):
        content = 'implementation("com.google.guava:guava")'
        deps = parse_gradle_kts(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['version'], '')


class TestGradleGroovy(unittest.TestCase):
    def test_single_quote_dep(self):
        content = "implementation 'com.google.guava:guava:31.1'"
        deps = parse_gradle_groovy(content, 'build.gradle')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['group'], 'com.google.guava')
        self.assertEqual(deps[0]['artifact'], 'guava')

    def test_double_quote_dep(self):
        content = 'implementation "com.google.guava:guava:31.1"'
        deps = parse_gradle_groovy(content, 'build.gradle')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['artifact'], 'guava')

    def test_map_notation(self):
        content = "implementation group: 'com.google.guava', name: 'guava', version: '31.1'"
        deps = parse_gradle_groovy(content, 'build.gradle')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['group'], 'com.google.guava')
        self.assertEqual(deps[0]['artifact'], 'guava')
        self.assertEqual(deps[0]['version'], '31.1')

    def test_project_dep(self):
        content = "implementation project(':core:common')"
        deps = parse_gradle_groovy(content, 'build.gradle')
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0]['is_internal'])
        self.assertEqual(deps[0]['module_path'], ':core:common')


class TestVersionCatalog(unittest.TestCase):
    def test_basic_catalog(self):
        content = '''
[versions]
guava = "31.1"

[libraries]
guava = { module = "com.google.guava:guava", version.ref = "guava" }
'''
        catalog = parse_version_catalog(content)
        self.assertIn('guava', catalog)
        self.assertEqual(catalog['guava'], 'com.google.guava:guava:31.1')

    def test_group_name_notation(self):
        content = '''
[versions]
slf4j = "2.0.0"

[libraries]
slf4j-api = { group = "org.slf4j", name = "slf4j-api", version.ref = "slf4j" }
'''
        catalog = parse_version_catalog(content)
        self.assertIn('slf4j.api', catalog)
        self.assertEqual(catalog['slf4j.api'], 'org.slf4j:slf4j-api:2.0.0')

    def test_resolve_catalog_refs(self):
        catalog = {'guava': 'com.google.guava:guava:31.1'}
        deps = [{
            'group': '', 'artifact': '', 'version': '',
            'scope': 'implementation', 'is_internal': False,
            'module_path': None, 'source_file': 'build.gradle.kts',
            'coordinate': 'libs.guava',
        }]
        resolved = resolve_catalog_refs(deps, catalog, 'build.gradle.kts')
        self.assertEqual(resolved[0]['group'], 'com.google.guava')
        self.assertEqual(resolved[0]['artifact'], 'guava')
        self.assertEqual(resolved[0]['version'], '31.1')


class TestSettingsGradle(unittest.TestCase):
    def test_single_include(self):
        content = 'include(":app")'
        modules = parse_settings_gradle(content)
        self.assertEqual(modules, [':app'])

    def test_multi_include(self):
        content = '''
include(":app", ":core", ":shared:utils")
'''
        modules = parse_settings_gradle(content)
        self.assertEqual(modules, [':app', ':core', ':shared:utils'])

    def test_deduplication(self):
        content = '''
include(":app")
include(":app")
'''
        modules = parse_settings_gradle(content)
        self.assertEqual(modules, [':app'])


class TestMavenPom(unittest.TestCase):
    def test_basic_pom(self):
        content = '''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <dependencies>
        <dependency>
            <groupId>com.google.guava</groupId>
            <artifactId>guava</artifactId>
            <version>31.1</version>
        </dependency>
    </dependencies>
</project>'''
        deps = parse_maven_pom(content, 'pom.xml')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['group'], 'com.google.guava')
        self.assertEqual(deps[0]['artifact'], 'guava')
        self.assertEqual(deps[0]['version'], '31.1')

    def test_property_interpolation(self):
        content = '''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <properties>
        <guava.version>31.1</guava.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>com.google.guava</groupId>
            <artifactId>guava</artifactId>
            <version>${guava.version}</version>
        </dependency>
    </dependencies>
</project>'''
        deps = parse_maven_pom(content, 'pom.xml')
        self.assertEqual(deps[0]['version'], '31.1')

    def test_scope(self):
        content = '''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
</project>'''
        deps = parse_maven_pom(content, 'pom.xml')
        self.assertEqual(deps[0]['scope'], 'test')


class TestPyprojectToml(unittest.TestCase):
    def test_basic_deps(self):
        content = '''
[project]
name = "my-project"
dependencies = [
    "requests>=2.28",
    "numpy",
    "pandas~=1.5",
]
'''
        deps = parse_pyproject_toml(content, 'pyproject.toml')
        self.assertEqual(len(deps), 3)
        names = [d['artifact'] for d in deps]
        self.assertIn('requests', names)
        self.assertIn('numpy', names)
        self.assertIn('pandas', names)
        for d in deps:
            self.assertEqual(d['group'], 'pypi')


class TestRequirementsTxt(unittest.TestCase):
    def test_basic_requirements(self):
        content = '''
requests>=2.28
numpy
# this is a comment
pandas~=1.5
'''
        deps = parse_requirements_txt(content, 'requirements.txt')
        self.assertEqual(len(deps), 3)

    def test_skip_flags(self):
        content = '''
-r base.txt
--index-url https://pypi.org/simple
requests
'''
        deps = parse_requirements_txt(content, 'requirements.txt')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['artifact'], 'requests')


class TestPackageJson(unittest.TestCase):
    def test_all_dep_types(self):
        content = '''{
    "dependencies": {"react": "^18.0.0", "lodash": "^4.17.0"},
    "devDependencies": {"jest": "^29.0.0"},
    "peerDependencies": {"react-dom": "^18.0.0"}
}'''
        deps = parse_package_json(content, 'package.json')
        self.assertEqual(len(deps), 4)

        scopes = {d['artifact']: d['scope'] for d in deps}
        self.assertEqual(scopes['react'], 'runtime')
        self.assertEqual(scopes['jest'], 'dev')
        self.assertEqual(scopes['react-dom'], 'peer')

    def test_empty_json(self):
        deps = parse_package_json('{}', 'package.json')
        self.assertEqual(deps, [])

    def test_invalid_json(self):
        deps = parse_package_json('not json', 'package.json')
        self.assertEqual(deps, [])


if __name__ == '__main__':
    unittest.main()
