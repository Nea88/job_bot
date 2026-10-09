from functools import lru_cache
from importlib import resources

import yaml


@lru_cache
def _aliases() -> dict[str, str]:
    raw = yaml.safe_load(resources.files("job_bot").joinpath("skills_aliases.yaml").read_text("utf-8"))
    mapping: dict[str, str] = {}
    for canonical, aliases in raw.items():
        mapping[canonical.lower()] = canonical
        for alias in aliases or []:
            mapping[alias.lower()] = canonical
    return mapping


def normalize_skill(name: str) -> str:
    key = " ".join(name.strip().split()).lower()
    return _aliases().get(key, key)
