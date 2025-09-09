# main.py
from ui_tk.app import App
from core.controller import MeasurementController

if __name__ == "__main__":
    controller = MeasurementController()
    app = App()
    app.set_controller(controller)
    app.mainloop()
