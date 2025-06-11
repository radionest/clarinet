class TestError(Exception):
    ...

a = {TestError():1,
     Exception():2}

try:
    raise TestError
except TestError as e:
    print(a[e])