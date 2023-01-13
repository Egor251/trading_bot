import numpy
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.dates import MONDAY, DateFormatter, DayLocator, WeekdayLocator
#TODO: fix install Ta-lib
#import talib as ta

import mplfinance
#from mplfinance import candlestick_ohlc
from mplfinance.original_flavor import candlestick_ohlc

import get_finance_data


class Metric:

    @staticmethod
    def display(dataframe, addition):
        '''apd = mplfinance.make_addplot(addition, type='scatter')
        mplfinance.plot(
            dataframe,
            type='candle',
            style='charles',
            ylabel='Price ($)',
            volume=True,
            ylabel_lower='Shares\nTraded', addplot=apd
        )'''
        mplfinance.plot(
            dataframe,
            type='candle',
            style='charles',
            ylabel='Price ($)',
            volume=True,
            ylabel_lower='Shares\nTraded'
        )

    def moving_average(self, data, n):

        data = pd.Series(data).rolling(window=n).mean().iloc[n - 1:].values
        print(data)
        return data

    @staticmethod
    def macd (data):
        #data["macd"], data["macd_signal"], data["macd_hist"] = ta.MACD(data['Close'])
        print(data["macd"])

if __name__ == "__main__":
    a = [20, 1, 345, 28, 36, 30]
    test = get_finance_data.Info().alphavantage('IBM', "5min")
    average = Metric().moving_average(a, 3)
    Metric.macd(test)
    #Metric.display(test)

