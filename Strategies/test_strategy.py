class test():
    def test(self, *param):
        optimization_parameters = {'a': range(5), 'default': range(10)}
        a = param[0]
        default = param[1]
        print(a, default)
        return a * default


if __name__ == '__main__':
    pass
