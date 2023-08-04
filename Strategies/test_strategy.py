from structy import Strat
from support_tools import Functionality
import os
import importlib.util
from database import DB
import asyncio


class Strategy(Strat):  # main class must be named "Strategy"

    # All necessary variables
    #abs_path = DB().get_abs_path()  Перенёс в structy
    algorithm = 'Test'  # Short description of strategies type
    description = 'This is a test strategy, it shows how strategy should looks like'  # Full description of strategy
    optimization_parameters = {'a': range(5), 'def': range(10)}  # В файле со стратегией должна быть переменная optimization_parameters содержащий словарь типа {переменная: range(x, y)} где range это диапазон значений для оптимизации
    default = [100, 120]  # default strategy parameters
    driver = None
    optimise = 0

    def run(self, class_code, ticker, param, optimise=0):  # main func must be named "run"
        self.optimise = optimise
        if len(param) < len(self.optimization_parameters):  #
            param = self.default
        a = param[0]  # put your strategy parameters here, but also you can do it in main func
        default = param[1]

        loop = asyncio.get_event_loop()
        loop.run_until_complete(Strategy().main('TQBR', 'SBER', a, default))

    async def main(self, class_code, ticker, a, default):
        # portfolio = self.get_portfolio()
        # dom = self.get_DOM(class_code, ticker)
        # dom = asyncio.create_task(self.get_DOM(class_code, ticker))
        # dom = self.get_DOM(class_code, ticker)

        while True:
            candles = asyncio.create_task(self.candles_stream(class_code, ticker))
            res = await asyncio.gather(candles)
            if res:
                print(res)
                self.set_transaction('', self.optimise)
            #return a * default

    def test(self):
        command = f"self.driver.{self.base_driver}().test()"
        print(eval(command))
        pass


if __name__ == '__main__':
    Strategy().run('TQBR', 'SBER', [1, 5])

    #print(Strategy().get_attr('optimization_parameters'))
    #Strategy().test()
    #asyncio.run(Strategy().run('TQBR', 'SBER', ''))
    #asyncio.run(Strategy().run('TQBR', 'SBER', ''))
    pass
 