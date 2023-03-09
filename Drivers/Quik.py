import os
from create_config import create_config
try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import os.path

import pandas as pd

from datetime import datetime

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


class Quik():

    qpProvider = QuikPy()  # Вызываем конструктор QuikPy с подключением к локальному компьютеру с QUIK

    path = "../settings.ini"
    config = configparser.ConfigParser()

    def print_callback(self, data):
        """Пользовательский обработчик события"""
        print(data)  # Печатаем полученные данные

    def close_connection(self):
        self.qpProvider.CloseConnectionAndThread()

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

    def SaveCandlesToFile(self, classCode='TQBR', secCodes=('SBER',), timeFrame='D', compression=1,
                          skipFirstDate=False, skipLastDate=False, fourPriceDoji=False):
        #TODO: Quik.SaveCandlesToFile: допилить
        """Получение баров, объединение с имеющимися барами в файле (если есть), сохранение баров в файл

        :param classCode: Код рынка
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
            fileName = f'{classCode}.{secCode}_{timeFrame}{compression}.txt'
            isFileExists = os.path.isfile(fileName)  # Существует ли файл
            if not isFileExists:  # Если файл не существует
                print(f'Файл {fileName} не найден и будет создан')
            else:  # Файл существует
                print(f'Получение файла {fileName}')
                fileBars = pd.read_csv(fileName, sep='\t', index_col='datetime')  # Считываем файл в DataFrame
                fileBars.index = pd.to_datetime(fileBars.index,
                                                format='%d.%m.%Y %H:%M')  # Переводим индекс в формат datetime
                print(f'- Первая запись файла: {fileBars.index[0]}')
                print(f'- Последняя запись файла: {fileBars.index[-1]}')
                print(f'- Кол-во записей в файле: {len(fileBars)}')

            newBars = self.qpProvider.GetCandlesFromDataSource(classCode, secCode, interval, 0)[
                "data"]  # Получаем все свечки
            pdBars = pd.DataFrame.from_dict(pd.json_normalize(newBars),
                                            orient='columns')  # Внутренние колонки даты/времени разворачиваем в отдельные колонки
            pdBars.rename(columns={'datetime.year': 'year', 'datetime.month': 'month', 'datetime.day': 'day',
                                   'datetime.hour': 'hour', 'datetime.min': 'minute', 'datetime.sec': 'second'},
                          inplace=True)  # Чтобы получить дату/время переименовываем колонки
            pdBars.index = pd.to_datetime(
                pdBars[['year', 'month', 'day', 'hour', 'minute', 'second']])  # Собираем дату/время из колонок
            pdBars = pdBars[['open', 'high', 'low', 'close', 'volume']]  # Отбираем нужные колонки
            pdBars.index.name = 'datetime'  # Ставим название индекса даты/времени
            pdBars.volume = pd.to_numeric(pdBars.volume, downcast='integer')  # Объемы могут быть только целыми
            if skipFirstDate:  # Если убираем бары на первую дату
                lenWithFirstDate = len(pdBars)  # Кол-во баров до удаления на первую дату
                firstDate = pdBars.index[0].date()  # Первая дата
                pdBars.drop(pdBars[(pdBars.index.date == firstDate)].index, inplace=True)  # Удаляем их
                print(f'- Удалено баров на первую дату {firstDate}: {lenWithFirstDate - len(pdBars)}')
            if skipLastDate:  # Если убираем бары на последнюю дату
                lenWithLastDate = len(pdBars)  # Кол-во баров до удаления на последнюю дату
                lastDate = pdBars.index[-1].date()  # Последняя дата
                pdBars.drop(pdBars[(pdBars.index.date == lastDate)].index, inplace=True)  # Удаляем их
                print(f'- Удалено баров на последнюю дату {lastDate}: {lenWithLastDate - len(pdBars)}')
            if not fourPriceDoji:  # Если удаляем дожи 4-х цен
                lenWithDoji = len(pdBars)  # Кол-во баров до удаления дожи
                pdBars.drop(pdBars[(pdBars.high == pdBars.low)].index,
                            inplace=True)  # Удаляем их по условия High == Low
                print('- Удалено дожи 4-х цен:', lenWithDoji - len(pdBars))
            print('- Первая запись в QUIK:', pdBars.index[0])
            print('- Последняя запись в QUIK:', pdBars.index[-1])
            print('- Кол-во записей в QUIK:', len(pdBars))

            if isFileExists:  # Если файл существует
                pdBars = pd.concat([fileBars, pdBars]).drop_duplicates(
                    keep='last').sort_index()  # Объединяем файл с данными из QUIK, убираем дубликаты, сортируем заново
            pdBars.to_csv(fileName, sep='\t', date_format='%d.%m.%Y %H:%M')
            print(f'- В файл {fileName} сохранено записей: {len(pdBars)}')

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

    def stream(self, class_code, sec_code):
        # TODO: stream: finish
        #qpProvider = QuikPy()  # Вызываем конструктор QuikPy с подключением к локальному компьютеру с QUIK
        # qpProvider = QuikPy(Host='<Ваш IP адрес>')  # Вызываем конструктор QuikPy с подключением к удаленному компьютеру с QUIK

        # classCode = 'TQBR'  # Класс тикера
        # secCode = 'GAZP'  # Тикер

        #classCode = 'SPBFUT'  # Класс тикера
        #secCode = 'SiH2'  # Для фьючерсов: <Код тикера><Месяц экспирации: 3-H, 6-M, 9-U, 12-Z><Последняя цифра года>

        # Запрос текущего стакана. Чтобы получать, в QUIK открыть Таблицу Котировки, указать тикер
        # print(f'Текущий стакан {classCode}.{secCode}:', qpProvider.GetQuoteLevel2(classCode, secCode)['data'])

        # Стакан. Чтобы отмена подписки работала корректно, в QUIK должна быть ЗАКРЫТА таблица Котировки тикера
        # qpProvider.OnQuote = PrintCallback  # Обработчик изменения стакана котировок
        # print(f'Подписка на изменения стакана {classCode}.{secCode}:', qpProvider.SubscribeLevel2Quotes(classCode, secCode)['data'])
        # print('Статус подписки:', qpProvider.IsSubscribedLevel2Quotes(classCode, secCode)['data'])
        # sleepSec = 3  # Кол-во секунд получения котировок
        # print('Секунд котировок:', sleepSec)
        # time.sleep(sleepSec)  # Ждем кол-во секунд получения котировок
        # print(f'Отмена подписки на изменения стакана:', qpProvider.UnsubscribeLevel2Quotes(classCode, secCode)['data'])
        # print('Статус подписки:', qpProvider.IsSubscribedLevel2Quotes(classCode, secCode)['data'])
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
        # TODO В QUIK 9.2.13.15 перестала работать повторная подписка на минутные бары. Остальные работают
        #  Перед повторной подпиской нужно перезапустить скрипт QuikSharp.lua Подписка станет первой, все заработает
        self.qpProvider.OnNewCandle = self.print_callback  # Обработчик получения новой свечки
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
        # TODO: stream: Должен выводить dataframe
        # Выход
        #self.qpProvider.CloseConnectionAndThread()  # Перед выходом закрываем соединение и поток QuikPy из любого экземпляра

    def get_all_accounts(self):

        # TODO: Quik: get_all_accounts: разобраться, подготовить вывод как dataframe
        """Получение всех торговых счетов"""
        futuresFirmId = 'SPBFUT'  # Фирма для фьючерсов. Измените, если требуется, на фирму, которую для фьючерсов поставил ваш брокер

        classCodes = self.qpProvider.GetClassesList()['data']  # Список классов
        classCodesList = classCodes[:-1].split(',')  # Удаляем последнюю запятую, разбиваем значения по запятой
        tradeAccounts = self.qpProvider.GetTradeAccounts()['data']  # Все торговые счета
        moneyLimits = self.qpProvider.GetMoneyLimits()['data']  # Все денежные лимиты (остатки на счетах)
        depoLimits = self.qpProvider.GetAllDepoLimits()['data']  # Все лимиты по бумагам (позиции по инструментам)
        orders = self.qpProvider.GetAllOrders()['data']  # Все заявки
        stopOrders = self.qpProvider.GetAllStopOrders()['data']  # Все стоп заявки

        # Коды клиента / Фирмы / Счета
        for tradeAccount in tradeAccounts:  # Пробегаемся по всем счетам
            firmId = tradeAccount['firmid']  # Фирма
            tradeAccountId = tradeAccount['trdaccid']  # Счет
            distinctClientCode = list(set([moneyLimit['client_code'] for moneyLimit in moneyLimits if
                                           moneyLimit['firmid'] == firmId]))  # Уникальные коды клиента по фирме
            print(
                f'Код клиента {distinctClientCode[0] if distinctClientCode else "не задан"}, Фирма {firmId}, Счет {tradeAccountId} ({tradeAccount["description"]})')
            trade_account_class_codes = tradeAccount['class_codes'][1:-1].split(
                '|')  # Классы торгового счета. Удаляем последнюю вертикальную черту, разбиваем значения по вертикальной черте
            intersection_class_codes = list(set(trade_account_class_codes).intersection(
                classCodesList))  # Классы, которые есть и в списке и в торговом счете
            # Классы
            for classCode in intersection_class_codes:  # Пробегаемся по всем общим классам
                class_info = self.qpProvider.GetClassInfo(classCode)['data']  # Информация о классе
                print(f'- Класс {classCode} ({class_info["name"]}), Тикеров {class_info["nsecs"]}')
                # Инструменты. Если выводить на экран, то занимают много места. Поэтому, закомментировали
                # classSecurities = qpProvider.GetClassSecurities(classCode)['data'][:-1].split(',')  # Список инструментов класса. Удаляем последнюю запятую, разбиваем значения по запятой
                # print(f'  - Тикеры ({classSecurities})')
            if firmId == futuresFirmId:  # Для фьючерсов свои расчеты
                # Лимиты
                print(
                    f'- Фьючерсный лимит {self.qpProvider.GetFuturesLimit(firmId, tradeAccountId, 0, "SUR")["data"]["cbplimit"]} SUR')
                # Позиции
                futuresHoldings = self.qpProvider.GetFuturesHoldings()['data']  # Все фьючерсные позиции
                activeFuturesHoldings = [futuresHolding for futuresHolding in futuresHoldings if
                                         futuresHolding['totalnet'] != 0]  # Активные фьючерсные позиции
                for activeFuturesHolding in activeFuturesHoldings:
                    print(
                        f'  - Фьючерсная позиция {activeFuturesHolding["sec_code"]} {activeFuturesHolding["totalnet"]} @ {activeFuturesHolding["cbplused"]}')
            else:  # Для остальных фирм
                # Лимиты
                firmMoneyLimits = [moneyLimit for moneyLimit in moneyLimits if
                                   moneyLimit['firmid'] == firmId]  # Денежные лимиты по фирме
                for firmMoneyLimit in firmMoneyLimits:  # Пробегаемся по всем денежным лимитам
                    limitKind = firmMoneyLimit['limit_kind']  # День лимита
                    print(
                        f'- Денежный лимит {firmMoneyLimit["tag"]} на T{limitKind}: {firmMoneyLimit["currentbal"]} {firmMoneyLimit["currcode"]}')
                    # Позиции
                    firmKindDepoLimits = [depoLimit for depoLimit in depoLimits if
                                          depoLimit['firmid'] == firmId and depoLimit['limit_kind'] == limitKind and
                                          depoLimit['currentbal'] != 0]  # Берем только открытые позиции по фирме и дню
                    for firmKindDepoLimit in firmKindDepoLimits:  # Пробегаемся по всем позициям
                        secCode = firmKindDepoLimit["sec_code"]  # Код тикера
                        classCode = self.qpProvider.GetSecurityClass(classCodes, secCode)['data']
                        entryPrice = float(firmKindDepoLimit["wa_position_price"])
                        lastPrice = float(self.qpProvider.GetParamEx(classCode, secCode, 'LAST')['data'][
                                              'param_value'])  # Последняя цена сделки
                        if classCode == 'TQOB':  # Для рынка облигаций
                            lastPrice *= 10  # Умножаем на 10
                        print(
                            f'  - Позиция {classCode}.{secCode} {firmKindDepoLimit["currentbal"]} @ {entryPrice:.2f}/{lastPrice:.2f}')
            # Заявки
            firmOrders = [order for order in orders if
                          order['firmid'] == firmId and order['flags'] & 0b1 == 0b1]  # Активные заявки по фирме
            for firmOrder in firmOrders:  # Пробегаемся по всем заявка
                isBuy = firmOrder['flags'] & 0b100 != 0b100  # Заявка на покупку
                print(
                    f'- Заявка номер {firmOrder["order_num"]} {"Покупка" if isBuy else "Продажа"} {firmOrder["class_code"]}.{firmOrder["sec_code"]} {firmOrder["qty"]} @ {firmOrder["price"]}')
            # Стоп заявки
            firmStopOrders = [stopOrder for stopOrder in stopOrders if stopOrder['firmid'] == firmId and stopOrder[
                'flags'] & 0b1 == 0b1]  # Активные стоп заявки по фирме
            for firmStopOrder in firmStopOrders:  # Пробегаемся по всем стоп заявкам
                isBuy = firmStopOrder['flags'] & 0b100 != 0b100  # Заявка на покупку
                print(
                    f'- Стоп заявка номер {firmStopOrder["order_num"]} {"Покупка" if isBuy else "Продажа"} {firmStopOrder["class_code"]}.{firmStopOrder["sec_code"]} {firmStopOrder["qty"]} @ {firmStopOrder["price"]}')


path = "../settings.ini"
if not os.path.exists(path):
    create_config(path)
config = configparser.ConfigParser()
config.read(path)


if __name__ == "__main__":
    #Quik().stream('TQBR', 'SBER')
    Quik().get_all_accounts()



#Quik.SaveCandlesToFile()








#Quik.close_connection(qpProvider)  # Перед выходом закрываем соединение и поток QuikPy из любого экземпляра