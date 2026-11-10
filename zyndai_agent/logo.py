import os
import re
from dataclasses import dataclass, field


@dataclass
class LogoVariant:
    url: str
    width: int
    height: int


@dataclass
class AgentLogos:
    default: str
    variants: list[LogoVariant] = field(default_factory=list)


def scan_logos(assets_dir: str, base_url: str) -> AgentLogos | None:
    """Scan assets_dir for logo.png and logo@WxH.png files.

    Returns None when assets/logo.png does not exist.
    File naming convention:
      logo.png           → default (served at GET /logo.png)
      logo@512x512.png   → size variant (served at GET /logo/512x512.png)
    """
    base = base_url.rstrip("/")

    if not os.path.isfile(os.path.join(assets_dir, "logo.png")):
        return None

    variants: list[LogoVariant] = []
    try:
        for fname in sorted(os.listdir(assets_dir)):
            m = re.match(r"^logo@(\d+)x(\d+)\.png$", fname)
            if not m:
                continue
            w, h = int(m.group(1)), int(m.group(2))
            variants.append(LogoVariant(url=f"{base}/logo/{w}x{h}.png", width=w, height=h))
    except OSError:
        pass

    return AgentLogos(default=f"{base}/logo.png", variants=variants)
