
class Module:
    path = ''

    def get_attr(self, attr):
        output = ''
        print('Я сделяль')
        if hasattr(self, str(attr)):
            command = f'self.{attr}'
            output = eval(command)
        return output

    def show(self):

        first_attr = ''
        second_attr = ''
        if hasattr(self, 'algorithm'):
            first_attr = self.algorithm
        if hasattr(self, 'description'):
            second_attr = self.description
        show_list = [first_attr, second_attr]
        return show_list


class Strat(Module):

    algorithm = ''
    description = ''
    optimization_parameters = ''
    default = ''

    def optimize(self):
        pass


class Driver(Module):

    description = ''

    def get_portfolio(self):
        pass


class Info(Module):

    description = ''

    def list(self):
        pass


if __name__ == '__main__':
    #Strategy().optimize()
    pass