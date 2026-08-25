"""Optional static and interactive result visualization."""

from .plots import generate_static_plots
from .replay import generate_replay_html

__all__ = ["generate_replay_html", "generate_static_plots"]
