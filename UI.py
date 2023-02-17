from support_tools import Tools

class UI():

    actions = ['use', 'run', 'set', 'help', 'exit', 'show', 'optimize']

    module_types = {'strategy': 'Strategies', 'driver': 'Drivers', 'info': 'Info'}

    current_state = ''


    def reader(self):
        while True:
            command = str(input("command: ")).split(' ')
            mod = command[0]
            if mod == 'exit':
                print('finishing program')
                break
            if mod not in self.actions:
                print('Invalid command')
            else:
                # Минутка космических решений
                eval('self.'+mod+'(command[1:])')

    def run(self, command):
        pass

    def use(self, command):
        try:
            type = self.module_types[command[0]]
        except KeyError:
            print('Unknown module type')
            return 0
        file = Tools.parse_dir(type)
        output = Tools.cut_py(file)
        if command[1] not in output:
            print('Unknown module')
            return 0
        file_dict = Tools.make_dict(output, file)

        self.current_state = f'{type}/{file_dict[command[1]]}'
        print(self.current_state)

    def set(self, command):
        pass

    def help(self, command):
        pass

    def show(self, command):
        data = self.module_types[command[0]]
        output = Tools.parse_dir(data)
        #TODO сделать красиво
        print(output)

    def optimize(self, command):
        pass


if __name__ == '__main__':
    UI().reader()


