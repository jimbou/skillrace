from mathlib import factorial, fib


def test_factorial_base_cases():
    assert factorial(0) == 1
    assert factorial(1) == 1


def test_factorial_five():
    assert factorial(5) == 120   # buggy implementation returns 24


def test_fib():
    assert fib(7) == 13
