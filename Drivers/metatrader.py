import MetaTrader5
from structy import Driver
try:
    import configparser
except ImportError:
    import ConfigParser as configparser


class Metatrader(Driver):

    path = "../settings.ini"
    config = configparser.ConfigParser()

    config.read(path)  # Чтение конфига
    metatrader_account = config['Account details']['Metatrader_account']
    metatrader_password = config['Account details']['Metatrader_password']

    # Function to start Meta Trader 5 (MT5)
    def start_mt5(self, server, path):
        # Ensure that all variables are the correct type
        uname = int(self.metatrader_account)  # Username must be an int
        pword = str(self.metatrader_password)  # Password must be a string
        trading_server = str(server)  # Server must be a string
        filepath = str(path)  # Filepath must be a string

        # Attempt to start MT5
        if MetaTrader5.initialize(login=uname, password=pword, server=trading_server, path=filepath):
            # Login to MT5
            if MetaTrader5.login(login=uname, password=pword, server=trading_server):
                return True
            else:
                print("Login Fail")
                quit()
                return PermissionError
        else:
            print("MT5 Initialization Failed")
            quit()
            return ConnectionAbortedError

if __name__ == '__main__':
    Metatrader().start_mt5()
