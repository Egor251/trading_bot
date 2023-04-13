from structy import Strat
from support_tools import Functionality
import os
import importlib.util
from database import DB

class Strategy(Strat):  # main class must be named "Strategy"

    # All necessary variables
    abs_path = DB().get_abs_path()
    algorithm = 'Test'  # Short description of strategies type
    description = 'Its a test strategy, it shows how strategy should looks like'  # Full description of strategy
    optimization_parameters = {'a': range(5), 'def': range(10)}  # В файле со стратегией должна быть переменная optimization_parameters содержащий словарь типа {переменная: range(x, y)} где range это диапазон значений для оптимизации
    default = [100, 120]  # default strategy parameters
    driver = None

    def get_candles(self, class_code, ticker, interval=1):
        candles = eval(f"self.driver.{self.base_driver}().get_candles({class_code}, {ticker}, {interval})")
        return candles

    def get_portfolio(self):
        portfolio = eval(f"self.driver.{self.base_driver}().get_portfolio()")
        return portfolio

    def get_DOM(self,  class_code, ticker):
        dom = eval(f"self.driver.{self.base_driver}().get_DOM()")
        return dom

    def __init__(self):
        super().__init__()
        self.driver = Functionality().import_module(f'{self.abs_path}/Drivers/{self.base_driver}.py')

    def run(self, param):  # main func must be named "run"
        if len(param) < len(self.optimization_parameters):
            param = self.default
        a = param[0]
        default = param[1]
        #print(a, default)
        return a * default

    def test(self):
        command = f"self.driver.{self.base_driver}().test()"
        print(eval(command))
        pass


if __name__ == '__main__':
    #Strategy().run([1, 5])
    #print(Strategy().get_attr('optimization_parameters'))
    #Strategy().test()
    Strategy().test()
    pass
