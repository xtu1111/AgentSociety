"""Lazy import citizen agent classes for AgentSociety community package.

This module mirrors the style used throughout the community package: each
agent has a tiny helper that imports the underlying class only when needed.
``__getattr__`` exposes the classes on demand and ``get_type_to_cls_dict``
provides a mapping from agent name to an importer callable for use by the
framework.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Type

from agentsociety.agent import CitizenAgentBase



if TYPE_CHECKING:  # pragma: no cover - hints for IDEs
    from .bdsc2025_track_one_envcitizen.track_one_envcitizen import TrackOneEnvCitizen
    from .bdsc2025_track_one_envambassador.baseline import BaselineEnvAmbassador
    from .bdsc2025_track_two_envcitizen.track_two_envcitizen import TrackTwoEnvCitizen
    from .bdsc2025_track_two_rumor_spreader.rumor_spreader import RumorSpreader
    from .cityagent import SocietyAgent
    from .polarization import AgreeAgent, DisagreeAgent
    from .marketing.marketing_agent import MarketingAgent


def _import_track_one_env_citizen() -> Type[CitizenAgentBase]:
    from .bdsc2025_track_one_envcitizen.track_one_envcitizen import TrackOneEnvCitizen

    return TrackOneEnvCitizen


def _import_track_one_env_ambassador() -> Type[CitizenAgentBase]:
    from .bdsc2025_track_one_envambassador.baseline import BaselineEnvAmbassador

    return BaselineEnvAmbassador


def _import_track_two_env_citizen() -> Type[CitizenAgentBase]:
    from .bdsc2025_track_two_envcitizen.track_two_envcitizen import TrackTwoEnvCitizen

    return TrackTwoEnvCitizen


def _import_rumor_spreader() -> Type[CitizenAgentBase]:
    from .bdsc2025_track_two_rumor_spreader.rumor_spreader import RumorSpreader

    return RumorSpreader

def _import_agree_agent() -> Type[CitizenAgentBase]:
    from .polarization import AgreeAgent

    return AgreeAgent

def _import_disagree_agent() -> Type[CitizenAgentBase]:
    from .polarization import DisagreeAgent

    return DisagreeAgent


def _import_society_agent() -> Type[CitizenAgentBase]:
    from .cityagent import SocietyAgent

    return SocietyAgent


def _import_marketing_agent() -> Type[CitizenAgentBase]:
    from .marketing.marketing_agent import MarketingAgent

    return MarketingAgent


def __getattr__(name: str) -> Type[CitizenAgentBase]:
    if name == "TrackOneEnvCitizen":
        return _import_track_one_env_citizen()
    if name == "TrackOneEnvAmbassador":
        return _import_track_one_env_ambassador()
    if name == "TrackTwoEnvCitizen":
        return _import_track_two_env_citizen()
    if name == "RumorSpreader":
        return _import_rumor_spreader()
    if name == "AgreeAgent":
         return _import_agree_agent()
    if name == "DisagreeAgent":
         return _import_disagree_agent()
    if name == "SocietyAgent":
        return _import_society_agent()
    if name == "MarketingAgent":
        return _import_marketing_agent()
    raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = [
    "TrackOneEnvCitizen",
    "TrackOneEnvAmbassador",
    "TrackTwoEnvCitizen",
    "RumorSpreader",
    "AgreeAgent",
    "DisagreeAgent",
    "SocietyAgent",
    "MarketingAgent",
]


def get_type_to_cls_dict() -> Dict[str, Callable[[], Type[CitizenAgentBase]]]:
    """Return mapping from agent type string to lazy import callables."""

    return {
        "TrackOneEnvCitizen": _import_track_one_env_citizen,
        "TrackOneEnvAmbassador": _import_track_one_env_ambassador,
        "TrackTwoEnvCitizen": _import_track_two_env_citizen,
        "RumorSpreader": _import_rumor_spreader,
        "AgreeAgent": _import_agree_agent,
        "DisagreeAgent": _import_disagree_agent,
        "SocietyAgent": _import_society_agent,
        "MarketingAgent": _import_marketing_agent,
    }
