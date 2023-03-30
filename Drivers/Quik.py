import os
import time
from create_config import create_config
try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import os.path

from structy import Driver

import pandas as pd

from datetime import datetime

from structy import Driver

from External_libs.QuikPy import QuikPy  # Работа с QUIK из Python через LUA скрипты QuikSharp

# End of imports
# TODO: pyfolio, bukosabino / та

#qpProvider = QuikPy()  # Вызываем конструктор QuikPy с подключением к локальному компьютеру с QUIK
# qpProvider = QuikPy(Host='<Ваш IP адрес>')  # Вызываем конструктор QuikPy с подключением к удаленному компьютеру с QUIK
#print(f'Подключено к терминалу QUIK по адресу: {qpProvider.Host}:{qpProvider.RequestsPort},{qpProvider.CallbacksPort}')

# QuikPy - Singleton класс. Будет создан 1 экземпляр класса, на него будут все ссылки
#qpProvider2 = QuikPy()  # QuikPy - это Singleton класс. При попытке создания нового экземпляра получим ссылку на уже имеющийся экземпляр
# qpProvider2 = QuikPy(Host='<Ваш IP адрес>')  # Вызываем конструктор QuikPy с подключением к удаленному компьютеру с QUIK
#print(f'Экземпляры класса совпадают: {qpProvider2 is qpProvider}')

# Проверка соединения
#print(f'Терминал QUIK подключен к серверу: {qpProvider.IsConnected()["data"] == 1}')
#print(f'Отклик QUIK на команду Ping: {qpProvider.Ping()["data"]}')  # Проверка работы скрипта QuikSharp. Должен вернуть Pong


class Quik(Driver):

    qpProvider = QuikPy()  # Вызываем конструктор QuikPy с подключением к локальному компьютеру с QUIK

    path = "../settings.ini"
    config = configparser.ConfigParser()

    @staticmethod
    def date_from_data(data):
        #string = f"{data['day']}.{data['month']}.{data['year']} {data['hour']}:{data['min']}"
        date = f"{data['day']}.{data['month']}.{data['year']}"
        time = f"{data['hour']}:{data['min']}"
        return date, time

    @staticmethod
    def print_callback(data):
        """Пользовательский обработчик события"""
        print(data)  # Печатаем полученные данные

    def close_connection(self):
        self.qpProvider.CloseConnectionAndThread()

    @staticmethod
    def ChangedConnection(data):
        """Пользовательский обработчик событий:
        - Соединение установлено
        - Соединение разорвано
        """
        print(f'{datetime.now().strftime("%d.%m.%Y %H:%M:%S")} - {data}')  # Печатаем полученные данные

    def set_transaction(self, transaction):

        def OnTransReply(data):
            """Обработчик события ответа на транзакцию пользователя"""
            print('OnTransReply')
            print(data['data'])  # Печатаем полученные данные

        def OnOrder(data):
            """Обработчик события получения новой / изменения существующей заявки"""
            print('OnOrder')
            print(data['data'])  # Печатаем полученные данные

        def OnTrade(data):
            """Обработчик события получения новой / изменения существующей сделки
            Не вызывается при закрытии сделки
            """
            print('OnTrade')
            print(data['data'])  # Печатаем полученные данные

        def OnFuturesClientHolding(data):
            """Обработчик события изменения позиции по срочному рынку"""
            print('OnFuturesClientHolding')
            print(data['data'])  # Печатаем полученные данные

        def OnDepoLimit(data):
            """Обработчик события изменения позиции по инструментам"""
            print('OnDepoLimit')
            print(data['data'])  # Печатаем полученные данные

        def OnDepoLimitDelete(data):
            """Обработчик события удаления позиции по инструментам"""
            print('OnDepoLimitDelete')
            print(data['data'])  # Печатаем полученные данные


        self.config.read(path)

        #config = configparser.ConfigParser()
        #qpProvider = QuikPy()  # Вызываем конструктор QuikPy с подключением к локальному компьютеру с QUIK
        # qpProvider = QuikPy(Host='<Ваш IP адрес>')  # Вызываем конструктор QuikPy с подключением к удаленному компьютеру с QUIK
        self.qpProvider.OnTransReply = OnTransReply  # Ответ на транзакцию пользователя. Если транзакция выполняется из QUIK, то не вызывается
        self.qpProvider.OnOrder = OnOrder  # Получение новой / изменение существующей заявки
        self.qpProvider.OnTrade = OnTrade  # Получение новой / изменение существующей сделки
        self.qpProvider.OnFuturesClientHolding = OnFuturesClientHolding  # Изменение позиции по срочному рынку
        self.qpProvider.OnDepoLimit = OnDepoLimit  # Изменение позиции по инструментам
        self.qpProvider.OnDepoLimitDelete = OnDepoLimitDelete

        '''class_code = 'SPBFUT'  # Код площадки
        sec_code = 'SiH2'  # Код тикера
        TransId = 12345  # Номер транзакции
        price = 77000  # Цена входа/выхода
        quantity = 1  # Кол-во в лотах'''

        #stopsteps = 10  # Размер проскальзывания в шагах цены
        stopsteps = self.config['Transactions']['stopsteps']
        slippage = float(self.qpProvider.GetSecurityInfo(transaction['class_code'], transaction['sec_code'])['data'][
                             'min_price_step']) * float(stopsteps)  # Размер проскальзывания в деньгах

        if slippage.is_integer():  # Целое значение проскальзывания мы должны отправлять без десятичных знаков
            slippage = int(slippage)  # поэтому, приводим такое проскальзывание к целому числу

        actions = {'NEW_STOP_ORDER': 'Новая стоп заявка', 'NEW_ORDER': 'Новая заявка', 'KILL_ORDER': 'Удалить заявку'}  # Список доступных действий (помещается в ключ ACTION)

        '''transaction = {  # Все значения должны передаваться в виде строк
            'TRANS_ID': str(TransId),  # Номер транзакции задается клиентом
            'CLIENT_CODE': '',  # Код клиента. Для фьючерсов его нет
            'ACCOUNT': 'SPBFUT00PST',  # Счет
            'ACTION': 'NEW_STOP_ORDER',  # Тип заявки: Новая стоп заявка
            'CLASSCODE': class_code,  # Код площадки
            'SECCODE': sec_code,  # Код тикера
            'OPERATION': 'B',  # B = покупка, S = продажа
            'PRICE': str(price),  # Цена исполнения
            'QUANTITY': str(quantity),  # Кол-во в лотах
            'STOPPRICE': str(price + slippage),  # Стоп цена исполнения
            'EXPIRY_DATE': 'GTC'}  # Срок действия до отмены'''
        return print(f'Новая стоп заявка отправлена на рынок: {self.qpProvider.SendTransaction(transaction)["data"]}')

    def SaveCandlesToFile(self, class_сode='TQBR', secCodes=('SBER',), timeFrame='D', compression=1,
                          skipFirstDate=False, skipLastDate=False, fourPriceDoji=False):
        #TODO: Quik.SaveCandlesToFile: допилить
        """Получение баров, объединение с имеющимися барами в файле (если есть), сохранение баров в файл

        :param class_сode: Код рынка
        :param secCodes: Коды тикеров в виде кортежа
        :param timeFrame: Временной интервал 'M'-Минуты, 'D'-дни, 'W'-недели, 'MN'-месяцы
        :param compression: Кол-во минут для минутного графика. Для остальных = 1
        :param skipFirstDate: Убрать бары на первую полученную дату
        :param skipLastDate: Убрать бары на последнюю полученную дату
        :param fourPriceDoji: Оставить бары с дожи 4-х цен
        """
        interval = compression  # Для минутных временнЫх интервалов ставим кол-во минут
        if timeFrame == 'D':  # Дневной временной интервал
            interval = 1440  # В минутах
        elif timeFrame == 'W':  # Недельный временной интервал
            interval = 10080  # В минутах
        elif timeFrame == 'MN':  # Месячный временной интервал
            interval = 23200  # В минутах

        for secCode in secCodes:  # Пробегаемся по всем тикерам
            #fileName = f'..\\..\\Data\\{class_code}.{sec_code}_{timeFrame}{compression}.txt'
            file_name = f'{class_сode}.{secCode}_{timeFrame}{compression}.txt'
            is_file_exists = os.path.isfile(file_name)  # Существует ли файл
            if not is_file_exists:  # Если файл не существует
                print(f'Файл {file_name} не найден и будет создан')
            else:  # Файл существует
                print(f'Получение файла {file_name}')
                file_bars = pd.read_csv(file_name, sep='\t', index_col='datetime')  # Считываем файл в DataFrame
                file_bars.index = pd.to_datetime(file_bars.index,
                                                format='%d.%m.%Y %H:%M')  # Переводим индекс в формат datetime
                print(f'- Первая запись файла: {file_bars.index[0]}')
                print(f'- Последняя запись файла: {file_bars.index[-1]}')
                print(f'- Кол-во записей в файле: {len(file_bars)}')

            new_bars = self.qpProvider.GetCandlesFromDataSource(class_сode, secCode, interval, 0)[
                "data"]  # Получаем все свечки
            pd_bars = pd.DataFrame.from_dict(pd.json_normalize(new_bars),
                                            orient='columns')  # Внутренние колонки даты/времени разворачиваем в отдельные колонки
            pd_bars.rename(columns={'datetime.year': 'year', 'datetime.month': 'month', 'datetime.day': 'day',
                                   'datetime.hour': 'hour', 'datetime.min': 'minute', 'datetime.sec': 'second'},
                          inplace=True)  # Чтобы получить дату/время переименовываем колонки
            pd_bars.index = pd.to_datetime(
                pd_bars[['year', 'month', 'day', 'hour', 'minute', 'second']])  # Собираем дату/время из колонок
            pd_bars = pd_bars[['open', 'high', 'low', 'close', 'volume']]  # Отбираем нужные колонки
            pd_bars.index.name = 'datetime'  # Ставим название индекса даты/времени
            pd_bars.volume = pd.to_numeric(pd_bars.volume, downcast='integer')  # Объемы могут быть только целыми
            if skipFirstDate:  # Если убираем бары на первую дату
                len_with_first_date = len(pd_bars)  # Кол-во баров до удаления на первую дату
                first_date = pd_bars.index[0].date()  # Первая дата
                pd_bars.drop(pd_bars[(pd_bars.index.date == first_date)].index, inplace=True)  # Удаляем их
                print(f'- Удалено баров на первую дату {first_date}: {len_with_first_date - len(pd_bars)}')
            if skipLastDate:  # Если убираем бары на последнюю дату
                len_with_last_date = len(pd_bars)  # Кол-во баров до удаления на последнюю дату
                last_date = pd_bars.index[-1].date()  # Последняя дата
                pd_bars.drop(pd_bars[(pd_bars.index.date == last_date)].index, inplace=True)  # Удаляем их
                print(f'- Удалено баров на последнюю дату {last_date}: {len_with_last_date - len(pd_bars)}')
            if not fourPriceDoji:  # Если удаляем дожи 4-х цен
                len_with_doji = len(pd_bars)  # Кол-во баров до удаления дожи
                pd_bars.drop(pd_bars[(pd_bars.high == pd_bars.low)].index,
                            inplace=True)  # Удаляем их по условия High == Low
                print('- Удалено дожи 4-х цен:', len_with_doji - len(pd_bars))
            print('- Первая запись в QUIK:', pd_bars.index[0])
            print('- Последняя запись в QUIK:', pd_bars.index[-1])
            print('- Кол-во записей в QUIK:', len(pd_bars))

            if is_file_exists:  # Если файл существует
                pd_bars = pd.concat([file_bars, pd_bars]).drop_duplicates(
                    keep='last').sort_index()  # Объединяем файл с данными из QUIK, убираем дубликаты, сортируем заново
            pd_bars.to_csv(file_name, sep='\t', date_format='%d.%m.%Y %H:%M')
            print(f'- В файл {file_name} сохранено записей: {len(pd_bars)}')

    def get_ticker(self, firm_id, class_code, sec_code):
        #firm_id = 'MC0063100000'  # Фирма
        #class_code = 'TQBR'  # Класс тикера
        #sec_code = 'SBER'  # Тикер

        # firm_id = 'SPBFUT'  # Фирма
        # class_code = 'SPBFUT'  # Класс тикера
        # sec_code = 'SiH2'  # Для фьючерсов: <Код тикера><Месяц экспирации: 3-H, 6-M, 9-U, 12-Z><Последняя цифра года>

        # Данные тикера
        securityInfo = self.qpProvider.GetSecurityInfo(class_code, sec_code)[
            "data"]  # Интерпретатор языка Lua, Таблица 4.21 Инструменты
        print(
            f'Информация о тикере {class_code}.{sec_code} ({securityInfo["short_name"]}):')  # Короткое наименование инструмента
        print('Валюта:', securityInfo['face_unit'])  # Валюта номинала
        print('Кол-во десятичных знаков:', securityInfo['scale'])  # Точность (количество значащих цифр после запятой)
        print('Лот:', securityInfo['lot_size'])  # Размер лота
        print('Шаг цены:', securityInfo['min_price_step'])  # Минимальный шаг цены

        # Торговый счет тикера
        tradeAccount = self.qpProvider.GetTradeAccount(class_code)["data"]  # Торговый счет для класса тикера
        print(f'Торговый счет для тикера класса {class_code}: {tradeAccount}')

        # Последняя цена сделки
        lastPrice = float(
            self.qpProvider.GetParamEx(class_code, sec_code, 'LAST')['data']['param_value'])  # Последняя цена сделки
        print('Последняя цена сделки:', lastPrice)

    def get_DOM(self, class_code, sec_code):

        dom = self.qpProvider.GetQuoteLevel2(class_code, sec_code)['data']
        print(dom)

    def get_candles(self, class_code, ticker, interval=1, quantity=1):
        output_dict = {'date': [], 'time': [], 'open': [], 'close': [], 'low': [], 'high': [], 'volume': [] }
        new_bars = self.qpProvider.GetCandlesFromDataSource(class_code, ticker, interval, quantity)["data"]  # Получаем все свечки
        # print(new_bars)
        for item in new_bars:
            date, time = self.date_from_data(item['datetime'])
            output_dict['date'].append(date)
            output_dict['time'].append(time)
            output_dict['open'].append(item['open'])
            output_dict['close'].append(item['close'])
            output_dict['low'].append(item['low'])
            output_dict['high'].append(item['high'])
            output_dict['volume'].append(item['volume'])
        candles = pd.DataFrame(output_dict)
        #print(candles)
        return candles


        #self.close_connection()

    def test_stream(self, class_code, ticker, interval=1):
        while True:
            self.get_candles(class_code, ticker, interval)
            time.sleep(interval*60)

    def stream(self, class_code, sec_code):
        # TODO: stream: Удалить к чёрту
        #qpProvider = QuikPy()  # Вызываем конструктор QuikPy с подключением к локальному компьютеру с QUIK
        # qpProvider = QuikPy(Host='<Ваш IP адрес>')  # Вызываем конструктор QuikPy с подключением к удаленному компьютеру с QUIK

        # class_сode = 'TQBR'  # Класс тикера
        # secCode = 'GAZP'  # Тикер

        #class_сode = 'SPBFUT'  # Класс тикера
        #secCode = 'SiH2'  # Для фьючерсов: <Код тикера><Месяц экспирации: 3-H, 6-M, 9-U, 12-Z><Последняя цифра года>

        # Запрос текущего стакана. Чтобы получать, в QUIK открыть Таблицу Котировки, указать тикер
        # print(f'Текущий стакан {class_сode}.{secCode}:', qpProvider.GetQuoteLevel2(class_сode, secCode)['data'])

        # Стакан. Чтобы отмена подписки работала корректно, в QUIK должна быть ЗАКРЫТА таблица Котировки тикера
        # qpProvider.OnQuote = PrintCallback  # Обработчик изменения стакана котировок
        # print(f'Подписка на изменения стакана {class_сode}.{secCode}:', qpProvider.SubscribeLevel2Quotes(class_сode, secCode)['data'])
        # print('Статус подписки:', qpProvider.IsSubscribedLevel2Quotes(class_сode, secCode)['data'])
        # sleepSec = 3  # Кол-во секунд получения котировок
        # print('Секунд котировок:', sleepSec)
        # time.sleep(sleepSec)  # Ждем кол-во секунд получения котировок
        # print(f'Отмена подписки на изменения стакана:', qpProvider.UnsubscribeLevel2Quotes(class_сode, secCode)['data'])
        # print('Статус подписки:', qpProvider.IsSubscribedLevel2Quotes(class_сode, secCode)['data'])
        # qpProvider.OnQuote = qpProvider.DefaultHandler  # Возвращаем обработчик по умолчанию

        # Обезличенные сделки. Чтобы получать, в QUIK открыть Таблицу обезличенных сделок, указать тикер
        # qpProvider.OnAllTrade = PrintCallback  # Обработчик получения обезличенной сделки
        # sleepSec = 1  # Кол-во секунд получения обезличенных сделок
        # print('Секунд обезличенных сделок:', sleepSec)
        # time.sleep(sleepSec)  # Ждем кол-во секунд получения обезличенных сделок
        # qpProvider.OnAllTrade = qpProvider.DefaultHandler  # Возвращаем обработчик по умолчанию

        # Просмотр изменений состояния соединения терминала QUIK с сервером брокера
        self.qpProvider.OnConnected = self.ChangedConnection  # Нажимаем кнопку "Установить соединение" в QUIK
        self.qpProvider.OnDisconnected = self.ChangedConnection  # Нажимаем кнопку "Разорвать соединение" в QUIK

        # Подписка на новые свечки. При первой подписке получим все свечки с начала прошлой сессии

        #  В QUIK 9.2.13.15 перестала работать повторная подписка на минутные бары. Остальные работают
        #  Перед повторной подпиской нужно перезапустить скрипт QuikSharp.lua Подписка станет первой, все заработает

        # Печатает свечи
        #self.qpProvider.OnNewCandle = self.print_callback  # Обработчик получения новой свечки

        for interval in (1,):  # (1, 60, 1440) = Минутки, часовки, дневки
            print(f'Подписка на интервал {interval}:',
                  self.qpProvider.SubscribeToCandles(class_code, sec_code, interval)['data'])
            print(f'Статус подписки на интервал {interval}:',
                  self.qpProvider.IsSubscribed(class_code, sec_code, interval)['data'])
        input('Enter - отмена\n')
        for interval in (60,):  # (1, 60, 1440) = Минутки, часовки, дневки
            print(f'Отмена подписки на интервал {interval}',
                  self.qpProvider.UnsubscribeFromCandles(class_code, sec_code, interval)['data'])
            print(f'Статус подписки на интервал {interval}:',
                  self.qpProvider.IsSubscribed(class_code, sec_code, interval)['data'])
        self.qpProvider.OnNewCandle = self.qpProvider.DefaultHandler  # Возвращаем обработчик по умолчанию

        self.qpProvider.OnConnected = self.qpProvider.DefaultHandler  # Возвращаем обработчик по умолчанию
        self.qpProvider.OnDisconnected = self.qpProvider.DefaultHandler  # Возвращаем обработчик по умолчанию
        # Выход
        #self.qpProvider.CloseConnectionAndThread()  # Перед выходом закрываем соединение и поток QuikPy из любого экземпляра

    def get_all_accounts(self):

        # TODO: Quik: get_all_accounts: добавить фьючерсы
        """Получение всех торговых счетов"""
        futures_firm_id = 'SPBFUT'  # Фирма для фьючерсов. Измените, если требуется, на фирму, которую для фьючерсов поставил ваш брокер

        class_codes = self.qpProvider.GetClassesList()['data']  # Список классов
        class_codes_list = class_codes[:-1].split(',')  # Удаляем последнюю запятую, разбиваем значения по запятой
        trade_accounts = self.qpProvider.GetTradeAccounts()['data']  # Все торговые счета
        money_limits = self.qpProvider.GetMoneyLimits()['data']  # Все денежные лимиты (остатки на счетах)
        depo_limits = self.qpProvider.GetAllDepoLimits()['data']  # Все лимиты по бумагам (позиции по инструментам)
        orders = self.qpProvider.GetAllOrders()['data']  # Все заявки
        stop_orders = self.qpProvider.GetAllStopOrders()['data']  # Все стоп заявки

        translation_order = {'Покупка': 'Buy', 'Продажа': 'Sell'}

        positions = {'Type': [], 'Class': [], 'Ticker': [], 'Quantity': [], 'Entry_price': [], 'Current_price': []}  # будущий dataframe

        order = {'Type': [], 'Class': [], 'Number': [], 'Ticker': [], 'Quantity': [], 'Price': []}

        # Коды клиента / Фирмы / Счета
        for trade_account in trade_accounts:  # Пробегаемся по всем счетам
            firm_id = trade_account['firmid']  # Фирма
            trade_account_id = trade_account['trdaccid']  # Счет
            distinct_client_code = list(set([money_limit['client_code'] for money_limit in money_limits if money_limit['firmid'] == firm_id]))  # Уникальные коды клиента по фирме
            print(
                f'Код клиента {distinct_client_code[0] if distinct_client_code else "не задан"}, Фирма {firm_id}, Счет {trade_account_id} ({trade_account["description"]})')
            trade_account_class_codes = trade_account['class_codes'][1:-1].split(
                '|')  # Классы торгового счета. Удаляем последнюю вертикальную черту, разбиваем значения по вертикальной черте
            intersection_class_codes = list(set(trade_account_class_codes).intersection(
                class_codes_list))  # Классы, которые есть и в списке и в торговом счете
            # Классы
            '''for class_code in intersection_class_codes:  # Пробегаемся по всем общим классам
                class_info = self.qpProvider.GetClassInfo(class_code)['data']  # Информация о классе
                print(f'- Класс {class_code} ({class_info["name"]}), Тикеров {class_info["nsecs"]}')
                # Инструменты. Если выводить на экран, то занимают много места. Поэтому, закомментировали
                # classSecurities = qpProvider.GetClassSecurities(class_сode)['data'][:-1].split(',')  # Список инструментов класса. Удаляем последнюю запятую, разбиваем значения по запятой
                # print(f'  - Тикеры ({classSecurities})')'''
            if firm_id == futures_firm_id:  # Для фьючерсов свои расчеты
                # Лимиты
                print(
                    f'- Фьючерсный лимит {self.qpProvider.GetFuturesLimit(firm_id, trade_account_id, 0, "SUR")["data"]["cbplimit"]} SUR')
                # Позиции
                futures_holdings = self.qpProvider.GetFuturesHoldings()['data']  # Все фьючерсные позиции
                active_futures_holdings = [futures_holding for futures_holding in futures_holdings if futures_holding['totalnet'] != 0]  # Активные фьючерсные позиции
                for activeFuturesHolding in active_futures_holdings:

                    print(
                        f'  - Фьючерсная позиция {activeFuturesHolding["sec_code"]} {activeFuturesHolding["totalnet"]} @ {activeFuturesHolding["cbplused"]}')
            else:  # Для остальных фирм
                # Лимиты
                firm_money_limits = [money_limit for money_limit in money_limits if money_limit['firmid'] == firm_id]  # Денежные лимиты по фирме
                for firmMoneyLimit in firm_money_limits:  # Пробегаемся по всем денежным лимитам
                    limit_kind = firmMoneyLimit['limit_kind']  # День лимита
                    #print(f'- Денежный лимит {firmMoneyLimit["tag"]} на T{limit_kind}: {firmMoneyLimit["currentbal"]} {firmMoneyLimit["currcode"]}')

                    if limit_kind == 365 and firmMoneyLimit["currcode"] not in positions['Ticker']:  # Добавляем в dataframe
                        positions['Type'].append('Money')
                        positions['Class'].append(firmMoneyLimit["tag"])
                        positions['Ticker'].append(firmMoneyLimit["currcode"])
                        positions['Quantity'].append(firmMoneyLimit["currentbal"])
                        positions['Entry_price'].append('0')
                        positions['Current_price'].append('0')

                    # Позиции
                    firm_kind_depo_limits = [depo_limit for depo_limit in depo_limits if depo_limit['firmid'] == firm_id and depo_limit['limit_kind'] == limit_kind and depo_limit['currentbal'] != 0]  # Берем только открытые позиции по фирме и дню
                    for firm_kind_depo_limit in firm_kind_depo_limits:  # Пробегаемся по всем позициям

                        if limit_kind == 365:
                            sec_code = firm_kind_depo_limit["sec_code"]  # Код тикера
                            class_code = self.qpProvider.GetSecurityClass(class_codes, sec_code)['data']
                            entry_price = float(firm_kind_depo_limit["wa_position_price"])
                            last_price = float(self.qpProvider.GetParamEx(class_code, sec_code, 'LAST')['data'][
                                                  'param_value'])  # Последняя цена сделки
                            type_name = 'Stock'
                            if class_code == 'TQOB' or class_code == 'TQCB':  # Для рынка облигаций
                                last_price *= 10  # Умножаем на 10
                                type_name = 'Bond'
                            if sec_code not in positions['Ticker']:  # Добавляем в dataframe
                                positions['Type'].append(type_name)
                                positions['Class'].append(class_code)
                                positions['Ticker'].append(sec_code)
                                positions['Quantity'].append(firm_kind_depo_limit["currentbal"])
                                positions['Entry_price'].append(entry_price)
                                positions['Current_price'].append(last_price)
                                #print(f'  - Позиция {class_code}.{sec_code} {firm_kind_depo_limit["currentbal"]} @ {entry_price:.2f}/{last_price:.2f}')

            # Заявки
            firm_orders = [order for order in orders if order['firmid'] == firm_id and order['flags'] & 0b1 == 0b1]  # Активные заявки по фирме
            for firmOrder in firm_orders:  # Пробегаемся по всем заявкам
                is_buy = firmOrder['flags'] & 0b100 != 0b100  # Заявка на покупку
                act = "Покупка" if is_buy else "Продажа"
                print(f'- Заявка номер {firmOrder["order_num"]} {"Покупка" if is_buy else "Продажа"} {firmOrder["class_code"]}.{firmOrder["sec_code"]} {firmOrder["qty"]} @ {firmOrder["price"]}')

                if firmOrder["sec_code"] not in order['Ticker']:  # Добавляем в dataframe
                    order['Type'].append('Limit')
                    order['Class'].append(translation_order[act])
                    order['Number'].append(firmOrder["order_num"])
                    order['Ticker'].append(firmOrder["sec_code"])
                    order['Quantity'].append(firmOrder["qty"])
                    order['Price'].append(firmOrder["price"])


            # Стоп заявки
            firm_stop_orders = [stopOrder for stopOrder in stop_orders if stopOrder['firmid'] == firm_id and stopOrder[
                'flags'] & 0b1 == 0b1]  # Активные стоп заявки по фирме
            for firm_stop_order in firm_stop_orders:  # Пробегаемся по всем стоп заявкам
                is_buy = firm_stop_order['flags'] & 0b100 != 0b100  # Заявка на покупку
                act = "Покупка" if is_buy else "Продажа"
                #print(f'- Стоп заявка номер {firm_stop_order["order_num"]} {"Покупка" if is_buy else "Продажа"} {firm_stop_order["class_code"]}.{firm_stop_order["sec_code"]} {firm_stop_order["qty"]} @ {firm_stop_order["price"]}')

                if firm_stop_order["sec_code"] not in order['Ticker']:  # Добавляем в dataframe
                    order['Type'].append('Stop')
                    order['Class'].append(translation_order[act])
                    order['Number'].append(firm_stop_order["order_num"])
                    order['Ticker'].append(firm_stop_order["sec_code"])
                    order['Quantity'].append(firm_stop_order["qty"])
                    order['Price'].append(firm_stop_order["price"])

        pos = pd.DataFrame(positions)
        orderss = pd.DataFrame(order)
        print(pos)
        print(orderss)
        self.close_connection()
        return pos, orderss


path = "../settings.ini"
if not os.path.exists(path):
    create_config(path)
config = configparser.ConfigParser()
config.read(path)


if __name__ == "__main__":
    #Quik().stream('TQBR', 'SBER')
    #Quik().get_all_accounts()
    #Quik().DOM_stream('TQBR', 'MTLR')
    #Quik().stream('TQBR', 'NLMK')
    Quik().get_candles('TQBR', 'SBER', 1, 1)
    #Quik().get_DOM('TQBR', 'SBER')



#Quik.SaveCandlesToFile()








#Quik.close_connection(qpProvider)  # Перед выходом закрываем соединение и поток QuikPy из любого экземпляра