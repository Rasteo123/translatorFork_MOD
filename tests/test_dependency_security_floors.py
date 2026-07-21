from pathlib import Path

from packaging.requirements import Requirement

import build_master


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECURITY_FLOORS = {
    "cryptography": "48.0.1",
    "defusedxml": "0.7.1",
    "idna": "3.15",
    "soupsieve": "2.8.4",
    "urllib3": "2.7.0",
}


def _requirements_by_name(filename):
    requirements = {}
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        requirements[requirement.name.lower()] = requirement
    return requirements


def test_runtime_requirements_exclude_known_vulnerable_versions():
    for filename in ("requirements.txt", "requirements-translator-only.txt"):
        requirements = _requirements_by_name(filename)
        for package, fixed_version in SECURITY_FLOORS.items():
            assert requirements[package].specifier.contains(fixed_version)


def test_generated_requirements_preserve_security_floors():
    for package, fixed_version in SECURITY_FLOORS.items():
        assert package in build_master.ESSENTIAL_PACKAGES
        forced_version = build_master.FORCED_VERSIONS.get(package)
        if forced_version is not None:
            requirement = Requirement(f"{package}{forced_version}")
            assert requirement.specifier.contains(fixed_version)
