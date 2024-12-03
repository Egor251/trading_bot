from database import DB
from support_tools import Functionality


class Module:  # супер базовый класс
    """
    Базовый класс для стратегий, драйверов или информационных модулей
    По сути своей мужен чтоб в каждом объекте были переменные path, my_name и my_type
    """
    path = ''
    my_name = ''
    my_type = ''

    def __init__(self):
        self.my_name = self.__class__.__name__  # Переменная my_name перезаписывается именем класса который наследуется от этого

    def set_default(self):  # Закидываем в БД текущий модуль как дефолтный. Так как эта функция наследуется, тот класс, из которого функция вызвана, и попадёт в БД
        """
        Закидываем в БД текущий модуль как дефолтный.
        Так как эта функция наследуется, тот класс, из которого функция вызвана, и попадёт в БД
        """
        DB().replace(self.my_type, self.my_name)

    def get_attr(self, attr):  # Не работает
        output = ''
        print('Я сделяль')
        if hasattr(self, str(attr)):
            command = f'self.{attr}'
            output = eval(command)
        return output

    def show(self):  # так как для стратегий, драйверов или информационных модулей в help выводится разное количество данных опишем всю логику здесь

        # Этот кусок для красивого отображения в меню. Ничего умного он не делает и на общую логику не влияет
        first_attr = ''
        second_attr = ''
        if hasattr(self, 'algorithm'):
            first_attr = self.algorithm
        if hasattr(self, 'description'):
            second_attr = self.description
        show_list = [first_attr, second_attr]
        return show_list


class Strat(Module):  # Класс стратегия
    """
    Базовый класс для объекта стратегия
    """
    my_type = 'strategy'
    algorithm = ''
    description = ''
    optimization_parameters = ''
    default = ''
    abs_path = DB().get_abs_path()

    base_driver = DB().select("SELECT state FROM main_db WHERE parameter = 'driver';")[0][0]
    #driver = Functionality().import_module(f'Drivers/{driver}.py')
    #eval(f'from Drivers import {driver}')

    def __init__(self):
        super().__init__()
        # Присваиваем переменной driver модуль с текущим драйвером из переменной base_driver
        self.driver = Functionality().import_module(f'{self.abs_path}/Drivers/{self.base_driver}.py')

    def optimize(self):
        """
        Оптимизация
        """
        pass

    def set_default(self):  # Так и должно быть. У стратегии нет функции set_default
        pass

    async def get_candles(self, class_code: str, ticker: str, interval=1):
        """
        Получаем свечки независимо от драйвера
        """
        candles = eval(f"self.driver.{self.base_driver}().get_candles('{class_code}', '{ticker}', {interval})")
        return candles

    async def get_portfolio(self):
        """
        Получаем портфель независимо от драйвера
        """
        portfolio = eval(f"self.driver.{self.base_driver}().get_portfolio()")
        return portfolio

    async def get_DOM(self, class_code, ticker):
        """
        Получаем DOM независимо от драйвера
        """
        command = f"self.driver.{self.base_driver}().get_DOM('{class_code}', '{ticker}')"
        dom = eval(command)
        return dom

    async def candles_stream(self, class_code, ticker, interval=1):
        """
        Получаем стрим свечек независимо от драйвера
        """
        stream = eval(f"self.driver.{self.base_driver}().candles_stream('{class_code}', '{ticker}, {interval}')")
        return stream

    def set_transaction(self, transaction, optimise=0):
        """
        Устанавливаем транзакцию
        """
        if optimise:
            trans = 1
            DB().insert()
            pass  # TODO structy: доделать перенос транзакции в оптимизатор
        else:
            command = f"self.driver.{self.base_driver}().set_transaction({transaction})"
            trans = eval(command)
        return trans


class Driver(Module):  # Класс драйвера
    """
    Базовый класс для объекта драйвер
    """
    my_type = 'driver'
    description = ''

    def list(self):
        pass


class Info(Module):  # класс для информационного модуля
    """
    Базовый класс для объекта информационного модуля
    """
    my_type = 'info'
    description = ''

    def list(self):
        pass


if __name__ == '__main__':
    #Strategy().optimize()
    print(Strat().base_driver)
    pass
