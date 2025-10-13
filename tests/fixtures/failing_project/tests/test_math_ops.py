from src.math_ops import divide, multiply


def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(-2, 5) == -10


def test_divide():
    assert divide(10, 2) == 5
    # This test will fail due to the assertion
    assert divide(7, 2) == 3


def test_divide_by_zero():
    # This test will fail due to uncaught exception
    result = divide(10, 0)
    assert result is None
