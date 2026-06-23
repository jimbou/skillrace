def factorial(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result


def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
