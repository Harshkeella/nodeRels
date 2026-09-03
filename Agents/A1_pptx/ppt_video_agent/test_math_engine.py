import unittest

from math_engine import analyze_math_text, solve_typed_problem


class MathEngineTests(unittest.TestCase):
    def test_linear_equation(self):
        answer = solve_typed_problem("Solve for x: 2x + 5 = 15")
        self.assertEqual(answer.result, "x = 5")
        self.assertEqual(answer.verification_status, "verified")

    def test_quadratic_equation(self):
        answer = solve_typed_problem("Solve x^2 - 5x + 6 = 0")
        self.assertIn("x = 2", answer.result)
        self.assertIn("x = 3", answer.result)
        self.assertEqual(answer.verification_status, "verified")

    def test_factor(self):
        answer = solve_typed_problem("Factor x^2 - 5x + 6")
        self.assertEqual(answer.result, "(x - 3)*(x - 2)")
        self.assertEqual(answer.verification_status, "verified")

    def test_derivative(self):
        answer = solve_typed_problem(
            "Differentiate x^3 + 2x with respect to x"
        )
        self.assertEqual(answer.result, "3*x**2 + 2")

    def test_indefinite_integral(self):
        answer = solve_typed_problem("Integrate 2x with respect to x")
        self.assertEqual(answer.result, "x**2 + C")
        self.assertEqual(answer.verification_status, "verified")

    def test_numeric_evaluation(self):
        answer = solve_typed_problem("Evaluate (12 + 8) / 4")
        self.assertEqual(answer.result, "5")

    def test_identity_equation(self):
        answer = solve_typed_problem("Solve 2(x + 1) = 2x + 2")
        self.assertIn("true for all values", answer.result)
        self.assertEqual(answer.verification_status, "verified")

    def test_inconsistent_equation(self):
        answer = solve_typed_problem("Solve 2x + 1 = 2x + 3")
        self.assertEqual(answer.result, "no solution")
        self.assertEqual(answer.verification_status, "verified")

    def test_simplification_preserves_domain_note(self):
        answer = solve_typed_problem("Simplify (x^2 - 1)/(x - 1)")
        self.assertEqual(answer.result, "x + 1")
        self.assertIn("x must not equal 1", answer.verification_details)

    def test_slide_analysis(self):
        report = analyze_math_text(
            "Linear equation\nSolve 3x - 7 = 11\nThe next slide contains a graph."
        )
        self.assertEqual(len(report["solved"]), 1)
        self.assertEqual(report["solved"][0]["result"], "x = 6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
