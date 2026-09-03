"""Deterministic math support for the PPT Video Agent.

The language model is used to explain answers, not to decide them.  This module
parses a deliberately small, safe subset of typed mathematics and asks SymPy to
compute and verify the result.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)


TRANSFORMATIONS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)

SAFE_FUNCTIONS = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "sqrt": sp.sqrt,
    "log": sp.log,
    "ln": sp.log,
    "exp": sp.exp,
    "abs": sp.Abs,
}

SAFE_CONSTANTS = {"pi": sp.pi, "E": sp.E}
SAFE_GLOBALS = {
    "__builtins__": {},
    "Symbol": sp.Symbol,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Rational": sp.Rational,
}

MATH_KEYWORDS = (
    "solve",
    "calculate",
    "evaluate",
    "simplify",
    "factor",
    "factorise",
    "factorize",
    "expand",
    "differentiate",
    "derivative",
    "integrate",
    "integral",
)


class MathParseError(ValueError):
    """Raised when a slide contains math outside the supported safe subset."""


@dataclass
class MathResult:
    source: str
    operation: str
    normalized_input: str
    result: str
    variable: str | None
    verification_status: str
    verification_details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_math(text: str) -> str:
    """Normalize common PowerPoint/Unicode math characters."""

    normalized = text.strip()
    replacements = {
        "−": "-",
        "–": "-",
        "×": "*",
        "·": "*",
        "÷": "/",
        "π": "pi",
        "²": "^2",
        "³": "^3",
        "⁴": "^4",
        "⁵": "^5",
        "⁶": "^6",
        "⁷": "^7",
        "⁸": "^8",
        "⁹": "^9",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)

    normalized = normalized.replace("\\cdot", "*")
    normalized = normalized.replace("\\times", "*")
    normalized = normalized.replace("\\div", "/")
    normalized = normalized.replace("\\pi", "pi")
    normalized = normalized.replace("**", "^")
    normalized = normalized.replace("$", "")
    normalized = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", normalized)
    normalized = re.sub(r"√\s*\(([^()]+)\)", r"sqrt(\1)", normalized)
    normalized = re.sub(r"√\s*([A-Za-z0-9.]+)", r"sqrt(\1)", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(" .;:?")


def _safe_parse(expression: str) -> sp.Expr:
    expression = normalize_math(expression)
    if not expression or len(expression) > 300:
        raise MathParseError("The expression is empty or too long.")
    if "=" in expression:
        raise MathParseError("Parse the two sides of an equation separately.")
    if not re.fullmatch(r"[0-9A-Za-z+\-*/^().,\s]+", expression):
        raise MathParseError("The expression contains unsupported characters.")

    identifiers = re.findall(r"[A-Za-z]+", expression)
    allowed_names = set(SAFE_FUNCTIONS) | set(SAFE_CONSTANTS)
    for name in identifiers:
        if name not in allowed_names and not (len(name) == 1 and name.isalpha()):
            raise MathParseError(f"Unsupported name: {name}")

    local_dict: dict[str, Any] = {**SAFE_FUNCTIONS, **SAFE_CONSTANTS}
    for name in identifiers:
        if len(name) == 1 and name not in local_dict:
            local_dict[name] = sp.Symbol(name)

    try:
        parsed = parse_expr(
            expression,
            local_dict=local_dict,
            global_dict=SAFE_GLOBALS,
            transformations=TRANSFORMATIONS,
            evaluate=True,
        )
    except Exception as exc:
        raise MathParseError(f"Could not parse '{expression}'.") from exc

    if not isinstance(parsed, sp.Expr):
        raise MathParseError("The input did not produce a mathematical expression.")
    return parsed


def _choose_variable(expressions: list[sp.Expr], requested: str | None = None) -> sp.Symbol:
    symbols = sorted(
        set().union(*(expression.free_symbols for expression in expressions)),
        key=lambda item: item.name,
    )
    if requested:
        requested_symbol = sp.Symbol(requested)
        if requested_symbol in symbols:
            return requested_symbol
    for preferred in ("x", "y", "z", "t", "n"):
        symbol = sp.Symbol(preferred)
        if symbol in symbols:
            return symbol
    if symbols:
        return symbols[0]
    return sp.Symbol(requested or "x")


def _strip_prefixes(text: str) -> str:
    cleaned = normalize_math(text)
    patterns = (
        r"^(?:example|question|problem)\s*\d*\s*[:.-]?\s*",
        r"^(?:please\s+)?(?:solve)(?:\s+for\s+[A-Za-z])?\s*[:.-]?\s*",
        r"^(?:what\s+is|find|calculate|evaluate)\s*[:.-]?\s*",
    )
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            updated = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
            if updated != cleaned:
                cleaned = updated.strip()
                changed = True
    return cleaned


def _requested_variable(text: str) -> str | None:
    patterns = (
        r"solve\s+for\s+([A-Za-z])",
        r"with\s+respect\s+to\s+([A-Za-z])",
        r"d\s*/\s*d([A-Za-z])",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None


def _equation_result(source: str) -> MathResult:
    cleaned = _strip_prefixes(source)
    if cleaned.count("=") != 1:
        raise MathParseError("Only one equation at a time is supported.")
    left_text, right_text = (part.strip() for part in cleaned.split("=", 1))
    left = _safe_parse(left_text)
    right = _safe_parse(right_text)
    variable = _choose_variable([left, right], _requested_variable(source))

    difference = sp.simplify(left - right)
    if difference == 0:
        return MathResult(
            source=source,
            operation="solve_equation",
            normalized_input=f"{sp.sstr(left)} = {sp.sstr(right)}",
            result="the equation is true for all values in its domain",
            variable=variable.name if (left.free_symbols or right.free_symbols) else None,
            verification_status="verified",
            verification_details="The difference between both sides simplifies to zero.",
        )

    if not difference.has(variable):
        return MathResult(
            source=source,
            operation="solve_equation",
            normalized_input=f"{sp.sstr(left)} = {sp.sstr(right)}",
            result="no solution",
            variable=variable.name if (left.free_symbols or right.free_symbols) else None,
            verification_status="verified",
            verification_details=(
                "The difference between both sides is nonzero and does not depend "
                "on the selected variable."
            ),
        )

    if left.atoms(sp.Function) or right.atoms(sp.Function):
        raise MathParseError(
            "Equations containing trigonometric, logarithmic, or exponential "
            "functions are not supported in this phase."
        )

    solutions = sp.solve(sp.Eq(left, right), variable)
    if not isinstance(solutions, list):
        solutions = [solutions]

    checks: list[str] = []
    all_verified = bool(solutions)
    for solution in solutions:
        residual = sp.simplify(difference.subs(variable, solution))
        checks.append(f"{variable} = {sp.sstr(solution)} gives residual {sp.sstr(residual)}")
        all_verified = all_verified and residual == 0

    if not solutions:
        result_text = "no solution"
        status = "verified"
        details = "The supported symbolic solver found an empty solution set."
    else:
        result_text = ", ".join(f"{variable} = {sp.sstr(item)}" for item in solutions)
        status = "verified" if all_verified else "not_verified"
        details = "; ".join(checks)

    return MathResult(
        source=source,
        operation="solve_equation",
        normalized_input=f"{sp.sstr(left)} = {sp.sstr(right)}",
        result=result_text,
        variable=variable.name,
        verification_status=status,
        verification_details=details,
    )


def _expression_after_keyword(source: str, keywords: tuple[str, ...]) -> str:
    keyword_pattern = "|".join(re.escape(keyword) for keyword in keywords)
    expression = re.sub(
        rf"^.*?(?:{keyword_pattern})(?:\s+of)?\s*[:.-]?\s*",
        "",
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    expression = re.sub(
        r"\s+with\s+respect\s+to\s+[A-Za-z]\s*$",
        "",
        expression,
        flags=re.IGNORECASE,
    )
    return normalize_math(expression)


def _calculus_result(source: str, operation: str) -> MathResult:
    variable_name = _requested_variable(source)
    if operation == "differentiate":
        expression_text = _expression_after_keyword(source, ("differentiate", "derivative"))
    else:
        expression_text = _expression_after_keyword(source, ("integrate", "integral"))

    expression = _safe_parse(expression_text)
    variable = _choose_variable([expression], variable_name)

    if operation == "differentiate":
        answer = sp.simplify(sp.diff(expression, variable))
        result_text = sp.sstr(answer)
        verification = "Computed directly with symbolic differentiation."
    else:
        answer = sp.integrate(expression, variable)
        residual = sp.simplify(sp.diff(answer, variable) - expression)
        if residual != 0:
            raise MathParseError("The antiderivative could not be verified.")
        result_text = f"{sp.sstr(answer)} + C"
        verification = (
            f"Differentiating {sp.sstr(answer)} gives {sp.sstr(expression)}; "
            "C is the integration constant."
        )

    return MathResult(
        source=source,
        operation=operation,
        normalized_input=sp.sstr(expression),
        result=result_text,
        variable=variable.name,
        verification_status="verified",
        verification_details=verification,
    )


def _transform_result(source: str, operation: str) -> MathResult:
    aliases = {
        "simplify": ("simplify",),
        "factor": ("factor", "factorise", "factorize"),
        "expand": ("expand",),
        "evaluate": ("evaluate", "calculate", "what is"),
    }
    expression_text = _expression_after_keyword(source, aliases[operation])
    expression = _safe_parse(expression_text)

    if operation == "simplify":
        answer = sp.simplify(expression)
    elif operation == "factor":
        answer = sp.factor(expression)
    elif operation == "expand":
        answer = sp.expand(expression)
    else:
        if expression.free_symbols:
            raise MathParseError("Numeric evaluation requires an expression without variables.")
        answer = sp.simplify(expression)

    residual = sp.simplify(answer - expression)
    verified = residual == 0
    restrictions = ""
    original_denominator = sp.denom(sp.together(expression))
    if original_denominator != 1:
        excluded_values = []
        for symbol in sorted(expression.free_symbols, key=lambda item: item.name):
            roots = sp.solve(sp.Eq(original_denominator, 0), symbol)
            if roots:
                values = ", ".join(sp.sstr(root) for root in roots)
                excluded_values.append(f"{symbol} must not equal {values}")
        if excluded_values:
            restrictions = " Domain restriction: " + "; ".join(excluded_values) + "."
    return MathResult(
        source=source,
        operation=operation,
        normalized_input=sp.sstr(expression),
        result=sp.sstr(answer),
        variable=None,
        verification_status="verified" if verified else "not_verified",
        verification_details=(
            f"Equivalent-expression residual: {sp.sstr(residual)}.{restrictions}"
        ),
    )


def solve_typed_problem(source: str) -> MathResult:
    """Solve one supported typed problem and return machine-verifiable evidence."""

    lowered = normalize_math(source).lower()
    if "differentiat" in lowered or "derivative" in lowered:
        return _calculus_result(source, "differentiate")
    if "integrat" in lowered or "integral" in lowered:
        return _calculus_result(source, "integrate")
    if re.search(r"\bfactor(?:ise|ize)?\b", lowered):
        return _transform_result(source, "factor")
    if re.search(r"\bexpand\b", lowered):
        return _transform_result(source, "expand")
    if re.search(r"\bsimplify\b", lowered):
        return _transform_result(source, "simplify")
    if "=" in lowered:
        return _equation_result(source)
    if any(keyword in lowered for keyword in ("evaluate", "calculate", "what is")):
        return _transform_result(source, "evaluate")
    raise MathParseError("No supported math operation was detected.")


def _candidate_lines(text: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in re.split(r"[\n;]+", text):
        line = raw_line.strip(" \t•-–")
        if not line or len(line) > 500:
            continue
        lowered = line.lower()
        has_keyword = any(keyword in lowered for keyword in MATH_KEYWORDS)
        looks_like_equation = (
            line.count("=") == 1
            and bool(re.search(r"[0-9A-Za-z)]\s*=\s*[0-9A-Za-z(+\-]", line))
        )
        if has_keyword or looks_like_equation:
            candidates.append(line)
    return candidates[:20]


def analyze_math_text(text: str) -> dict[str, Any]:
    """Find and verify supported typed problems in extracted slide text."""

    solved: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for candidate in _candidate_lines(text):
        key = normalize_math(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            solved.append(solve_typed_problem(candidate).to_dict())
        except MathParseError as exc:
            skipped.append({"source": candidate, "reason": str(exc)})
        except Exception as exc:
            skipped.append(
                {
                    "source": candidate,
                    "reason": f"SymPy could not verify this problem: {type(exc).__name__}",
                }
            )

    return {"solved": solved, "skipped": skipped}
