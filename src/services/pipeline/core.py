from typing import List, Dict, Callable, Any
from abc import ABC, abstractmethod

class PipelineError(Exception):
    ...
                    

class Condition(ABC):
    @abstractmethod
    def check(self, result) -> bool: ...


class ConditionTrue(Condition):
    def check(self, result) -> bool:
        return True
    
class ConditionError(Condition):
    def __init__(self, exception: Exception):
        self.exception = exception
    def check(self, result) -> bool:
        return type(result) is self.exception
            


class ConditionalStep:
    step: "Step"
    condition: Condition


class Step:
    name: str
    next_steps: Dict[Condition, List["Step"]]
    handler: Callable
    task: str

    def run(self, msg):
        result = self.handle_msg(msg)
        self.create_next_steps(result)

    def handle_msg(self, msg) -> Any:
        try:
            res = self.handler(msg)
            return res
        except Exception as e:
            return e

    def create_next_steps(self, result: Any):
        for condition, steps in self.next_steps.items():
            if condition.check(result):
                map(lambda s: kick(s.step), steps)

    # -------------

    def add_next(self, condition, step): ...

    def on_error(self, exception: Exception):
        return ConditionalStep(step=self, 
                               condition=Condition(exception))

    def __gt__(self, other: Self):
        self.next_steps[ConditionTrue()].append(other)

    def __lt__(self, other: ConditionalStep):
        other.step.add_next(condition=other.condition, step=self)

step = Step()
step2 = Step() 
step3 = Step()

step.on_error(PipelineError) > step2

step.on_result(is_equal=False) > step3
