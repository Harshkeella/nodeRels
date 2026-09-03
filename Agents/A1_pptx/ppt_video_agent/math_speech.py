"""Convert common mathematical notation into text that TTS can pronounce."""

from __future__ import annotations

import re


LATEX_WORDS = {
    "alpha": "alpha",
    "beta": "beta",
    "gamma": "gamma",
    "delta": "delta",
    "epsilon": "epsilon",
    "zeta": "zeta",
    "eta": "eta",
    "theta": "theta",
    "iota": "iota",
    "kappa": "kappa",
    "lambda": "lambda",
    "mu": "mu",
    "nu": "nu",
    "xi": "xi",
    "omicron": "omicron",
    "pi": "pi",
    "rho": "rho",
    "sigma": "sigma",
    "tau": "tau",
    "upsilon": "upsilon",
    "phi": "phi",
    "chi": "chi",
    "psi": "psi",
    "omega": "omega",
    "Gamma": "gamma",
    "Delta": "delta",
    "Theta": "theta",
    "Lambda": "lambda",
    "Xi": "xi",
    "Pi": "pi",
    "Sigma": "sigma",
    "Phi": "phi",
    "Psi": "psi",
    "Omega": "omega",
    "times": "times",
    "cdot": "times",
    "div": "divided by",
    "pm": "plus or minus",
    "mp": "minus or plus",
    "le": "less than or equal to",
    "leq": "less than or equal to",
    "ge": "greater than or equal to",
    "geq": "greater than or equal to",
    "ne": "not equal to",
    "neq": "not equal to",
    "approx": "approximately equal to",
    "infty": "infinity",
    "sum": "the sum",
    "prod": "the product",
    "int": "the integral",
    "partial": "partial",
    "sin": "sine",
    "cos": "cosine",
    "tan": "tangent",
    "log": "log",
    "ln": "natural log",
    "exp": "exponential",
}

UNICODE_WORDS = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    "Γ": "gamma",
    "Δ": "delta",
    "Θ": "theta",
    "Λ": "lambda",
    "Ξ": "xi",
    "Π": "pi",
    "Σ": "sigma",
    "Φ": "phi",
    "Ψ": "psi",
    "Ω": "omega",
    "×": " times ",
    "÷": " divided by ",
    "±": " plus or minus ",
    "≤": " less than or equal to ",
    "≥": " greater than or equal to ",
    "≠": " not equal to ",
    "≈": " approximately equal to ",
    "∞": " infinity ",
    "∑": " the sum ",
    "∏": " the product ",
    "∫": " the integral ",
    "∂": " partial ",
    "√": " square root of ",
}


def _replace_simple_latex_commands(text: str) -> str:
    def command_replacement(match: re.Match[str]) -> str:
        command = match.group(1)
        return f" {LATEX_WORDS.get(command, command)} "

    return re.sub(r"\\([A-Za-z]+)", command_replacement, text)


def _replace_braced_command(text: str, command: str, replacement: str) -> str:
    """Replace non-nested one-argument LaTeX commands repeatedly."""

    pattern = re.compile(rf"\\{command}\s*\{{([^{{}}]+)\}}")
    previous = None
    while text != previous:
        previous = text
        text = pattern.sub(lambda match: f" {replacement} {match.group(1)} ", text)
    return text


def _replace_fractions(text: str) -> str:
    pattern = re.compile(r"\\(?:d?frac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
    previous = None
    while text != previous:
        previous = text
        text = pattern.sub(
            lambda match: f" {match.group(1)} divided by {match.group(2)} ",
            text,
        )
    return text


def math_to_speech(text: str) -> str:
    """Return narration text without raw LaTeX/backslash pronunciation."""

    spoken = text

    # Remove Markdown and LaTeX display delimiters.
    spoken = spoken.replace("```", " ").replace("`", " ")
    spoken = spoken.replace("**", "").replace("__", "")
    spoken = spoken.replace("$", "")
    spoken = spoken.replace(r"\(", " ").replace(r"\)", " ")
    spoken = spoken.replace(r"\[", " ").replace(r"\]", " ")
    spoken = spoken.replace(r"\left", " ").replace(r"\right", " ")

    # Powers must be handled before braces are removed.
    spoken = re.sub(r"\^\s*\\circ", " degrees", spoken)
    spoken = re.sub(r"\^\s*\{\s*2\s*\}", " squared", spoken)
    spoken = re.sub(r"\^\s*\{\s*3\s*\}", " cubed", spoken)
    spoken = re.sub(
        r"\^\s*\{\s*([^{}]+)\s*\}",
        lambda match: f" to the power of {match.group(1)}",
        spoken,
    )
    spoken = re.sub(r"\^\s*2\b", " squared", spoken)
    spoken = re.sub(r"\^\s*3\b", " cubed", spoken)
    spoken = re.sub(
        r"\^\s*([A-Za-z0-9.+-]+)",
        lambda match: f" to the power of {match.group(1)}",
        spoken,
    )

    # Common structured LaTeX.
    spoken = _replace_fractions(spoken)
    spoken = _replace_braced_command(spoken, "sqrt", "square root of")
    spoken = re.sub(
        r"_\s*\{\s*([^{}]+)\s*\}",
        lambda match: f" sub {match.group(1)}",
        spoken,
    )
    spoken = re.sub(r"_\s*([A-Za-z0-9]+)", r" sub \1", spoken)

    # Named LaTeX and Unicode symbols.
    spoken = _replace_simple_latex_commands(spoken)
    for symbol, word in UNICODE_WORDS.items():
        spoken = spoken.replace(symbol, f" {word} ")

    # Operators that may still be present as plain characters.
    spoken = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " divided by ", spoken)
    spoken = re.sub(r"\s*=\s*", " equals ", spoken)
    spoken = re.sub(r"\s+\+\s+", " plus ", spoken)
    spoken = re.sub(r"\s+-\s+", " minus ", spoken)
    spoken = re.sub(r"\s+\*\s+", " times ", spoken)
    spoken = spoken.replace("^", " to the power of ")

    # Never pass braces or a remaining backslash to the speech engine.
    spoken = spoken.replace("{", " ").replace("}", " ")
    spoken = spoken.replace("\\", " ")
    spoken = re.sub(r"\s+", " ", spoken)
    spoken = re.sub(r"\s+([,.;:!?])", r"\1", spoken)
    return spoken.strip()

