"""
Jinja2 template renderer for notification messages.

Templates are loaded from the ``app/templates/`` directory relative to the
application package.  Each template file is named:

    ``{category}/{name}.{lang}.j2``

For example:
    ``user/otp.en.j2``
    ``booking/confirmed.ar.j2``

Supported languages: ``"en"`` (English), ``"ar"`` (Arabic).
Falls back to ``"en"`` if the requested language template does not exist.

Template variables are rendered safely — Jinja2 autoescape is disabled for
SMS/WhatsApp (plain text) but enabled for HTML email templates.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from app.core.exceptions import TemplateRenderError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Path to the templates directory
_TEMPLATES_DIR = Path(__file__).parent

# Two Jinja2 environments: plain-text (SMS/WhatsApp) and HTML (email)
_plain_env: Environment | None = None
_html_env: Environment | None = None


def _get_plain_env() -> Environment:
    global _plain_env  # noqa: PLW0603
    if _plain_env is None:
        _plain_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
            keep_trailing_newline=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _plain_env


def _get_html_env() -> Environment:
    global _html_env  # noqa: PLW0603
    if _html_env is None:
        _html_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "htm"]),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _html_env


class TemplateRenderer:
    """Renders Jinja2 templates to strings.

    Usage::

        renderer = TemplateRenderer()
        body = renderer.render(
            "user/otp",
            lang="ar",
            context={"customer_name": "أحمد", "otp": "123456"},
        )
    """

    def render(
        self,
        template_name: str,
        *,
        lang: str,
        context: dict,
        is_html: bool = False,
    ) -> str:
        """Render a template to a string.

        Args:
            template_name: Template key without language or extension
                (e.g. ``"user/otp"``).
            lang: Language code — ``"en"`` or ``"ar"``.
            context: Variables passed to the template.
            is_html: When ``True``, use the HTML-safe Jinja2 environment
                with autoescaping enabled.

        Returns:
            Rendered string.

        Raises:
            TemplateRenderError: If the template cannot be found or rendered.
        """
        env = _get_html_env() if is_html else _get_plain_env()

        # Try requested language first, then fall back to English
        candidates = [f"{template_name}.{lang}.j2"]
        if lang != "en":
            candidates.append(f"{template_name}.en.j2")

        # If template_name has channel suffix (e.g. _sms, _whatsapp, _email), also fall back to base template
        for suffix in ("_sms", "_whatsapp", "_email"):
            if template_name.endswith(suffix):
                base_name = template_name[: -len(suffix)]
                candidates.append(f"{base_name}.{lang}.j2")
                if lang != "en":
                    candidates.append(f"{base_name}.en.j2")
                break

        for candidate in candidates:
            try:
                template = env.get_template(candidate)
                rendered = template.render(**context).strip()
                logger.debug(
                    "Template rendered",
                    template=candidate,
                    lang=lang,
                    preview=rendered[:60],
                )
                return rendered
            except TemplateNotFound:
                continue
            except Exception as exc:
                raise TemplateRenderError(candidate, exc) from exc

        raise TemplateRenderError(
            f"{template_name}.{lang}.j2",
            FileNotFoundError(
                f"No template found for {template_name!r} in languages {candidates}"
            ),
        )

    def render_subject(
        self,
        template_name: str,
        *,
        lang: str,
        context: dict,
    ) -> str:
        """Render an email subject template (suffix ``_subject``).

        Tries ``{template_name}_subject.{lang}.j2`` first.
        Falls back to an empty string if the subject template doesn't exist.
        """
        try:
            return self.render(f"{template_name}_subject", lang=lang, context=context)
        except TemplateRenderError:
            return ""
