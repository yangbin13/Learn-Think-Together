import math

import pytest

from backend.app.calculator import calculate


def test_truss_force_balance_uses_angles_and_equations():
    # Two cables at 30/60 degrees balance a 10 kN load: test both equilibrium equations.
    left = calculate("10*cos(radians(60))/sin(radians(30)+radians(60))")["result"]
    right = calculate("10*cos(radians(30))/sin(radians(30)+radians(60))")["result"]
    assert left == pytest.approx(5)
    assert right == pytest.approx(5 * math.sqrt(3))
    assert -left * math.cos(math.pi/6) + right * math.cos(math.pi/3) == pytest.approx(0, abs=1e-12)
    assert left * math.sin(math.pi/6) + right * math.sin(math.pi/3) == pytest.approx(10)


def test_angle_convention_conversion_and_radian_input():
    assert calculate("sin(pi/6)")["result"] == pytest.approx(.5)
    assert calculate("sin(radians(30))")["result"] == pytest.approx(.5)
    assert calculate("sin(30)")["result"] == pytest.approx(math.sin(30))
    assert calculate("degrees(pi/2)")["result"] == pytest.approx(90)
    assert "弧度" in calculate("cos(pi)")["angle_convention"]


def test_math_and_original_arithmetic_remain_bounded():
    assert calculate("sqrt(3)*10/2")["result"] == pytest.approx(5 * math.sqrt(3))
    assert calculate("abs(-6)+7//2+7%2")["result"] == 10
    assert calculate("100*10**5")["result"] == 10_000_000
    assert calculate("2**12")["result"] == 4096
    assert calculate("tan(radians(45))")["result"] == pytest.approx(1)


@pytest.mark.parametrize("expression", [
    "sqrt(-1)", "tan(pi/2)", "sin(1000001)", "1/0", "2**13", "1000001**2",
    "10**12*10**12", "sin(1e100)", "1e1000", "1e15+1", "True", "1+2j",
    "__import__('os').system('whoami')", "math.sin(0)", "pi.__class__",
    "sin(x=0)", "sin(0,1)", "sin(*[0])", "[1][0]", "sum([1,2])",
    "lambda: 1", "(x:=1)", "", "1+", "-"*22+"1",
])
def test_domain_limits_and_code_execution_are_rejected(expression):
    with pytest.raises(ValueError):
        calculate(expression)
