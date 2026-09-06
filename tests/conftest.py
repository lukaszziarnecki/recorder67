import gc
import os
import pytest

# Ustawienie platformy offscreen domyślnie, o ile nie skonfigurowano inaczej
if "QT_QPA_PLATFORM" not in os.environ:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        QApplication = None


@pytest.fixture(scope="session")
def qapp():
    """Provides a headless QApplication instance for Qt-dependent tests with clean teardown."""
    if QApplication is None:
        pytest.skip("Neither PySide6 nor PyQt6 is available")

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    try:
        from recorder.ui import theme
        theme.apply_theme(app, "classic_dark")
    except Exception:
        pass

    yield app

    # Stabilny teardown Qt: czyszczenie schowka, zamykanie widgetów, opróżnienie zdarzeń i GC
    try:
        try:
            from PySide6.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            if cb is not None:
                cb.clear()
        except Exception:
            try:
                from PyQt6.QtGui import QGuiApplication
                cb = QGuiApplication.clipboard()
                if cb is not None:
                    cb.clear()
            except Exception:
                pass

        app.processEvents()
        for widget in list(app.topLevelWidgets()):
            try:
                widget.close()
                widget.deleteLater()
            except Exception:
                pass
        app.processEvents()
    except Exception:
        pass
    finally:
        gc.collect()


def pytest_unconfigure(config):
    """Clean up Qt clipboard and event loop at the very end of pytest execution."""
    try:
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.clear()
    except Exception:
        pass

    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
            for widget in list(app.topLevelWidgets()):
                try:
                    widget.close()
                    widget.deleteLater()
                except Exception:
                    pass
            app.processEvents()
    except Exception:
        pass

    import gc
    gc.collect()

