"""Hermes Core domain services for the Rental Manager operational agent."""

from rental_manager.services.hermes.events import emit_domain_event, install_domain_event_listeners

__all__ = ["emit_domain_event", "install_domain_event_listeners"]
