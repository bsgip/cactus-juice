from my_app.app import example_fn


def test_example_fn():
    """This is an example test to just show how pytest can discover and run tests"""
    assert example_fn() == "result"
