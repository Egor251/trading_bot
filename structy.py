
class Module():
    path = ''


class Strategy(Module):

    def optimize(self):
        pass


class Driver(Module):

    def get_portfolio(self):
        pass


class Info(Module):

    def list(self):
        pass
