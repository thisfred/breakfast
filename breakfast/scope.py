class Scope:

    def __init__(self,
                 path=tuple(),
                 direct_class=None,
                 parent=None,
                 _self=None):
        self.path = path
        self.direct_class = direct_class
        self.parent = parent
        self._self = _self

    def get_name(self, name):
        return self.path + (name,)

    def enter_scope(self, name, direct_class=None):
        return Scope(
            path=self.path + (name,),
            direct_class=direct_class or self.direct_class,
            parent=self)

    @property
    def in_class_scope(self):
        return (
            self.direct_class and
            self.direct_class != self.parent.direct_class)
