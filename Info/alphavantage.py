import requests
import pandas as pd
try:
    import configparser
except ImportError:
    import ConfigParser as configparser


class Alphavantage():

    # api_key = 'demo'  # for debug
    path = "../settings.ini"
    config = configparser.ConfigParser()
    config.read(path)  # Чтение конфига
    api_key = config['API keys']['alphavantage']

    def get_tickers_list(self):
        url = f'https://www.alphavantage.co/query?function=LISTING_STATUS&apikey={self.api_key}'  # возвращает CSV
        r = requests.get(url)
        print(r)

    def get_candles(self, ticker, interval='1h'):
        # WORKS!
        url = f'https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol={ticker}&interval={interval}&apikey={self.api_key}'
        #print(url)
        r = requests.get(url)
        output_data = r.json()  # {Meta Data :{...}, Временной период : {цена открытия: ..., верхняя цена:..., нижняя цена:..., цена закрытия:..., объём:...}}
        tmp_data = output_data[list(output_data.keys())[1]]  # Вышла бы слишком длинная и сложная строка
        # временные списки для формирования DataFrame
        date = []
        time = []
        opened = []
        high = []
        low = []
        close = []
        volume = []
        # формирование DataFrame
        for key in list(tmp_data.keys()):
            date_time = key.split(' ')
            # print(tmp_data[key])
            date.append(date_time[0])
            time.append(date_time[1])
            opened.append(float(tmp_data[key]['1. open']))
            high.append(float(tmp_data[key]['2. high']))
            low.append(float(tmp_data[key]['3. low']))
            close.append(float(tmp_data[key]['4. close']))
            volume.append(float(tmp_data[key]['5. volume']))

        result_dict = {'date': date,
                       'time': time,
                       'open': opened,
                       'high': high,
                       'low': low,
                       'close': close,
                       'volume': volume}

        data = pd.DataFrame(result_dict)

        data.index = pd.DatetimeIndex(data['date'])
        # print(data)
        return data


if __name__ == '__main__':
    Alphavantage().get_tickers_list()
