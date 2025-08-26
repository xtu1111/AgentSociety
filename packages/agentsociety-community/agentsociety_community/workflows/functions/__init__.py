from typing import TYPE_CHECKING, Awaitable, Callable, Dict, Type

from agentsociety.simulation import AgentSociety

FunctionType = Callable[[AgentSociety], Awaitable[None]]

if TYPE_CHECKING:
    from .bdsc_2025_track_two.workflows import (
        init_simulation_context_bdsc_2025_track_two,
        send_and_gather_survey_results_bdsc_2025_track_two)
    from .common.workflow import do_nothing


def _import_do_nothing() -> Type[FunctionType]:
    from .common.workflow import do_nothing

    return do_nothing


def _import_init_simulation_context_bdsc_2025_track_two() -> Type[FunctionType]:
    from .bdsc_2025_track_two.workflows import \
        init_simulation_context_bdsc_2025_track_two

    return init_simulation_context_bdsc_2025_track_two


def _import_send_and_gather_survey_results_bdsc_2025_track_two() -> Type[FunctionType]:
    from .bdsc_2025_track_two.workflows import \
        send_and_gather_survey_results_bdsc_2025_track_two

    return send_and_gather_survey_results_bdsc_2025_track_two

def _import_insert_citizen_information_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import insert_citizen_information

    return insert_citizen_information

def _import_gather_carbon_emission_results_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import gather_carbon_emission_results

    return gather_carbon_emission_results

def _import_gather_promotion_results_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import gather_promotion_results

    return gather_promotion_results

def _import_gather_communication_history_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import gather_communication_history

    return gather_communication_history


def _import_delete_ambassador_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import delete_ambassador

    return delete_ambassador


def _import_start_emission_log_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import start_emission_log

    return start_emission_log


def _import_send_carbon_awareness_survey_bdsc_2025_track_one() -> Type[FunctionType]:
    from .bdsc_2025_track_one.workflow import send_carbon_awareness_survey

    return send_carbon_awareness_survey


def _import_marketing_report_sentiment() -> Type[FunctionType]:
    from .marketing.workflow import report_sentiment
    return report_sentiment

def __getattr__(name: str) -> Type[FunctionType]:
    __all__ = [
        "do_nothing",
        "insert_citizen_information_bdsc_2025_track_one",
        "gather_carbon_emission_results_bdsc_2025_track_one",
        "gather_promotion_results_bdsc_2025_track_one",
        "gather_communication_history_bdsc_2025_track_one",
        "delete_ambassador_bdsc_2025_track_one",
        "start_emission_log_bdsc_2025_track_one",
        "send_carbon_awareness_survey_bdsc_2025_track_one",
        "marketing_setup_agents",
        "marketing_send_advertisement",
        "marketing_send_rumor",
        "marketing_send_rebuttal",
        "marketing_report_sentiment",
        "init_simulation_context_bdsc_2025_track_two",
        "send_and_gather_survey_results_bdsc_2025_track_two",
    ]


def get_type_to_cls_dict() -> Dict[str, Callable[[], Type[FunctionType]]]:
    """
    Use this function to get all the functions.
    """
    return {
        "do_nothing": _import_do_nothing,
        "insert_citizen_information_bdsc_2025_track_one": _import_insert_citizen_information_bdsc_2025_track_one,
        "gather_carbon_emission_results_bdsc_2025_track_one": _import_gather_carbon_emission_results_bdsc_2025_track_one,
        "gather_promotion_results_bdsc_2025_track_one": _import_gather_promotion_results_bdsc_2025_track_one,
        "gather_communication_history_bdsc_2025_track_one": _import_gather_communication_history_bdsc_2025_track_one,
        "delete_ambassador_bdsc_2025_track_one": _import_delete_ambassador_bdsc_2025_track_one,
        "start_emission_log_bdsc_2025_track_one": _import_start_emission_log_bdsc_2025_track_one,
        "send_carbon_awareness_survey_bdsc_2025_track_one": _import_send_carbon_awareness_survey_bdsc_2025_track_one,
        "marketing_setup_agents": _import_marketing_setup_agents,
        "marketing_send_advertisement": _import_marketing_send_advertisement,
        "marketing_send_rumor": _import_marketing_send_rumor,
        "marketing_send_rebuttal": _import_marketing_send_rebuttal,
        "marketing_report_sentiment": _import_marketing_report_sentiment,
        "init_simulation_context_bdsc_2025_track_two": _import_init_simulation_context_bdsc_2025_track_two,
        "send_and_gather_survey_results_bdsc_2025_track_two": _import_send_and_gather_survey_results_bdsc_2025_track_two,
    }
