import itertools
import json
import runpy
import os
import sys
from subprocess import Popen, PIPE

class Optimizer():

    def run_module(self, file):

        process = Popen(
            # ["python", "-O", "hello.py"]
            ["python", file],
            stdout=PIPE, stderr=PIPE, universal_newlines=True
        )
        print(process.stdout.read())

    def optimize(self, file, optimize_range=range(1)):
        with open(file) as func:
            parse_parameter = []
            for line in func:
                if line.find('optimization_parameters') != -1:  # В файле со стратегией должна быть переменная optimization_parameters содержащий словарь типа {переменная: range(x, y)} где range это диапазон значений для оптимизации
                    tmp = line.split('=')  # Да, это должна быть переменная, парсим по строкам и выделяем словарь по наличию =
                    tmp = tmp[-1].replace(' ', '')  # На всякий случай пробелы уберём
                    parse_parameter = eval(tmp)  # преобразуем строку со словарём в реальный словарь (стоит ли боспокоиться об инъекциях в этот параметр?)
                    #print(parse_parameter)
                    #print(list(parse_parameter.values()))
                    ranges = list(parse_parameter.values())  # выделяем все range из словаря и загоняем в массив

            for rang in ranges:  # Ищем самый большой range из стратегии
                if len(rang) > len(optimize_range):
                    optimize_range = rang

            param = list(itertools.permutations(optimize_range, int(len(parse_parameter))))  # формируем комбинации из параметров для оптимизации
            remove_list = []

            for i in range(len(ranges)):  # Оптимизируем оптимизатор:) itertools не может генерировать комбинации из нескольких разных наборов значений. чтобы не тестировать то, что не нужно уберём лишние значения
                for par in param:
                    #print(i, ranges[i], par)
                    if par[i] not in ranges[i]:
                        remove_list.append(par)  # массив значений для удаления

            for par in remove_list:  # удаляем ненужные значения
                param.remove(par)
            print(param)

            sys.path.append('strategies')

            # TODO: Optimizer: научиться передавать аргументы в функцию
            # Cледующая строка тоже работает, но не ясно как передавать параметры в функцию
            # runpy.run_path(file, run_name='__main__')
            self.run_module(file)


if __name__ == '__main__':
    file = 'strategies/test_strategy.py'
    Optimizer().optimize(file)
