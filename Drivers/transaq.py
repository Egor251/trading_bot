import ctypes, os, sys
from structy import Driver
dll = ctypes.cdll.LoadLibrary("C:\\Users\\Egor\\Documents\GitHub\\trading_bot\\External_libs\\txmlconnector64.dll")
answer = dll.Initialize(".", 3)
print("Initialize : ", answer)

class Transaq(Driver):
    dll = ctypes.cdll.LoadLibrary(
        "C:\\Users\\Egor\\Documents\GitHub\\trading_bot\\External_libs\\txmlconnector64.dll")

    def Initialise(self):
        answer = self.dll.Initialize(".", 3)
        print("Initialize : ", answer)

    def connect(self):
        connect_command = '''<command id="connect">
        <login>19411</login>
        <password>2704</password>
        <host>tr1.finam.ru</host>
        <port>3900</port>
        <language>ru</language>
        <autopos>false</autopos>
        <micex_registers>false</micex_registers>
        <milliseconds>false</milliseconds>
        <utc_time>true</utc_time>
        <rqdelay>20</rqdelay>
        <session_timeout>300</session_timeout>
        <request_timeout>5</request_timeout>
        <push_u_limits>2</push_u_limits>
        <push_pos_equity>2</push_pos_equity>
        </command>'''

        answer = self.dll.SendCommand(bytes(connect_command, 'utf-8'))
        print('Он говорит: ', answer)

    def get_history_date(self, ticker):
        command = f'''< commandid = "gethistorydata" >
        < security >
        < board > идентификатор режима торгов < / board >
        < seccode > {ticker} < / seccode >
        < / security >
        < period > идентификатор периода < / period >
        < count > количество свечей < / count >
        < reset > true < / reset >
        < / command >'''


    def UnInitialize(self):
        self.dll.UnInitialize()

if __name__ == '__main_-':
    Transaq().Initialise()