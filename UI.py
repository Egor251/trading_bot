import time
from support_tools import Tools, Functionality
from database import DB


class UI:

    def __init__(self):
        print('System check')
        time.sleep(2)
        if DB().test_connection():  # на самом деле это нафиг не нужно, и БД всегда будет подключена. Это костыль чтобы БД точно создалась в корневой папке если её ещё нет
            print('Database connected')  # ну и выглядит круто, наверное...

    actions = ['use', 'run', 'set', 'help', 'exit', 'show',
               'optimize']  # Перечень доступных команд (первых слов в команде)

    module_types = {'strategy': 'Strategies', 'driver': 'Drivers', 'info': 'Info',
                    'options': 'options'}  # Перечень вторых слов

    column_names = {'strategy': ['module name', 'algorithm', 'description'],
                    'driver': ['module name', 'description'],
                    'info': ['module name', 'description'],
                    'help': ['key', 'explanation'],
                    'options': ['Parameter', 'Value']}

    current_state = ''

    current_print = 'Command'  # выводим в консоль перед каждой командой текущий модуль с которым мы работаем. Если модуль не выбран - выводим Command

    def reader(self):
        while True:
            command = str(input(f"{self.current_print}: ")).split(
                ' ')  # Входные данные, разбиваем их по пробелам и формируем массив
            mod = command[0]  # Первое слово это основная команда
            if mod == 'exit':  # Выход из программы
                print('finishing program')
                time.sleep(2)
                break
            if mod not in self.actions:  # проверка на существование введённой команды
                print('Invalid command')
            else:
                # Минутка космических решений
                eval(
                    'self.' + mod + '(command[1:])')  # вызываем функцию текущего класса с соответствующим названием и передаём остаток команды в качестве аргумента

    def run(self, command):
        pass

    def use(self, command):  # фунцкия меняет текущий модуль с которым работаем
        if command == 'options':
            print('Unknown module type')
            return 0
        try:
            type = self.module_types[command[0]]  # типы модуля могут быть только strategy, driver и info
        except KeyError:
            print('Unknown module type')
            return 0
        file = Tools.parse_dir(type)  # находим все модули в папке модулей соответствующего типа
        output = Tools.cut_py(file)  # для красоты отрезаем .py
        if len(command) < 2:  # проверка на то, что указан только тип модуля, сам модуль не указан
            print('You missed last argument')
            print(f'Running "show {command[0]}" instead')
            self.show([command[0]])  # Если так и есть, то запускаем show и выводим список доступных модулей
        else:
            if command[1] not in output:
                print('Unknown module')  # Проверка на правильность ввода названия модуля
                return 0
            file_dict = Tools.make_dict(output,
                                        file)  # Создаём словарь {имя_модуля: имя_модуля.py} (так просто удобней)
            self.current_state = f'{type}/{file_dict[command[1]]}'  # присваиваем будущей команде первую её часть - запускаемый модуль
            self.current_print = self.current_state[:-3]  # Меняем надпись, выводящуюся перед input на текущий модуль
            # print(self.current_state)

    def set(self, command):
        print(self.current_state)

    def help(self, command):
        help_table = []
        help_table.append(['use',
                           'select type of module and what module you want to work with (use strategy your_strategy, use driver Quik, use info yahoo etc.'])
        help_table.append(['show', 'shows allowed modules or parameters. keywords: strategy, driver, info, options'])
        help_table.append(['help', 'shows this help menu'])
        help_table.append(['exit', 'Finish the program'])

        Tools().make_table(self.column_names['help'], help_table)  # Табличка

    def show(self, command):
        table = []

        # Отдельный паттерн поведения при получении ключевого слова options
        if command[0] == 'options':
            if self.current_print == 'Command':  # Проверяем, выбран ли какой-либо модуль
                print('Unable to show options, no module was selected')
                return 0
            else:
                print(f'Changable parameters for {self.current_print}')
                default = Tools.parse_file(self.current_state, 'default')  # Парсим дефолтные значения из модуля

                optimization_parameters = Tools.parse_file(self.current_state,
                                                           'optimization_parameters')  # Парсим параметры, которые можно менять в модуле
                # TODO: отказаться от parse_file
                # optimization_parameters = Functionality().run_module(self.current_state, 'get_attr', 'optimization_parameters')  # Пока не работает

                if len(optimization_parameters) == 0:  # Проверка, а есть ли параметры, которые можно менять
                    print('There no options to set')
                    return
                else:
                    optimization_parameters = eval(
                        optimization_parameters)  # Вместо парсинга строки просто преобразуем строку в словарь
                    keys = optimization_parameters.keys()  # Берём только ключи из словаря
                if len(default) == 0:  # Проверяем указаны ли дефолтные значения
                    default = []
                    for _ in optimization_parameters:
                        default.append(
                            'None')  # Если дефолтных значений нет, а параметры, которые можно указать, есть, то в табличке выведем None у дефолтных значений
                else:
                    default = eval(default)  # Вместо парсинка строки просто преобразуем строку в массив
                    for index, item in enumerate(keys):
                        table.append([item, default[index]])  # добавляем данные в будущую табличку

        # вывод списка доступных модулей
        else:
            data = self.module_types[command[0]]  # Получаем тип модуля
            output = Tools.parse_dir(data)  # Парсим папку с модулями выбранного типа
            for file in output:
                path = data + '/' + file  # формируем путь к файлу
                '''algorithm = Tools.parse_file(path, 'algorithm')  # Парсим переменную algorithm - короткое указание типа модуля
                description = Tools.parse_file(path, 'description')  # Парсим переменную description - полное описание модуля модуля
                table.append([file[:-3], algorithm, description])  # заводим данные в табличку'''
                # print(Functionality().run_module(path, '', 'show'))
                table.append([file[:-3], *Functionality().run_module(path, 'show', '')])

        Tools.make_table(self.column_names[command[0]], table)  # Табличка

    def optimize(self, command):
        pass


if __name__ == '__main__':
    UI().reader()
