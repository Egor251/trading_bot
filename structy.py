from database import DB
from support_tools import Functionality


class Module:  # супер базовый класс
    path = ''
    my_name = ''
    my_type = ''

    def __init__(self):
        self.my_name = self.__class__.__name__  # Переменная my_name перезаписывается именем класса который наследуется от этого

    def set_default(self):  # Закидываем в БД текущий модуль как дефолтный. Так как эта функция наследуется, тот класс, из которого функция вызвана, и попадёт в БД
        DB().replace(self.my_type, self.my_name)

    def get_attr(self, attr):  # Не работает
        output = ''
        print('Я сделяль')
        if hasattr(self, str(attr)):
            command = f'self.{attr}'
            output = eval(command)
        return output

    def show(self):  # так как для стратегий, драйверов или информационных модулей в help выводится разное количество данных опишем всю логику здесь

        first_attr = ''
        second_attr = ''
        if hasattr(self, 'algorithm'):
            first_attr = self.algorithm
        if hasattr(self, 'description'):
            second_attr = self.description
        show_list = [first_attr, second_attr]
        return show_list


class Strat(Module):  # Класс стратегия
    my_type = 'strategy'
    algorithm = ''
    description = ''
    optimization_parameters = ''
    default = ''

    base_driver = DB().select("SELECT state FROM main_db WHERE parameter = 'driver';")[0][0]
    #driver = Functionality().import_module(f'Drivers/{driver}.py')
    #eval(f'from Drivers import {driver}')

    def optimize(self):
        pass

    def set_default(self):  # Так и должно быть. У стратегии нет функции set_default
        pass


class Driver(Module):  # Класс драйвера

    my_type = 'driver'
    description = ''


class Info(Module):  # класс для информационного модуля

    my_type = 'info'
    description = ''

    def list(self):
        pass


if __name__ == '__main__':
    #Strategy().optimize()
    print(Strat().base_driver)
    pass