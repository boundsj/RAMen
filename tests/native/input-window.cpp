// Original native driver: event-loop timing and input-method commits, no reads.
#include <QGuiApplication>
#include <QClipboard>
#include <QQuickView>
#include <QQuickItem>
#include <QTimer>
#include <QDateTime>
#include <QInputMethodEvent>
#include <QKeyEvent>
#include <cstdio>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    QGuiApplication app(argc, argv);
    QQuickView view;
    view.setSource(QUrl::fromLocalFile(QString::fromLocal8Bit(argv[1])));
    if (view.status() == QQuickView::Error) {
        for (const auto &error : view.errors()) std::fprintf(stderr, "%s\n", qPrintable(error.toString()));
        return 3;
    }
    view.show();
    view.requestActivate();
    QObject::connect(app.clipboard(), &QClipboard::dataChanged, []() {
        std::fprintf(stderr, "CHANGED %lld\n", QDateTime::currentMSecsSinceEpoch());
        std::fflush(stderr);
    });
    QTimer::singleShot(500, [&]() {
        std::fprintf(stderr, "PLATFORM %s ACTIVE %d QT %s\n", qPrintable(app.platformName()), view.isActive(), qVersion());
        if (QString::fromLocal8Bit(argv[2]) == "ime") {
            auto *field = view.rootObject()->findChild<QObject *>("testedField");
            if (!field || !view.focusObject()) { app.exit(4); return; }
            QInputMethodEvent preedit(QString::fromUtf8("に"), {});
            QCoreApplication::sendEvent(view.focusObject(), &preedit);
            QInputMethodEvent commit;
            commit.setCommitString(QString::fromUtf8("日本🍜"));
            QCoreApplication::sendEvent(view.focusObject(), &commit);
            if (field->property("text").toString() != QString::fromUtf8("日本🍜")) { app.exit(5); return; }
            QMetaObject::invokeMethod(field, "select", Q_ARG(QVariant, 1), Q_ARG(QVariant, 2));
            QInputMethodEvent tooLong;
            tooLong.setCommitString(QString::fromUtf8("too-long-for-this-field"));
            QCoreApplication::sendEvent(view.focusObject(), &tooLong);
            if (field->property("text").toString() != QString::fromUtf8("日本🍜")
                || field->property("selectionStart").toInt() != 1
                || field->property("selectionEnd").toInt() != 2) { app.exit(6); return; }
            QMetaObject::invokeMethod(field, "undo");
            if (!field->property("text").toString().isEmpty()) { app.exit(7); return; }
            std::fprintf(stderr, "IME AND REJECTED-EDIT UNDO OK\n");
            QKeyEvent menuKey(QEvent::KeyPress, Qt::Key_Menu, Qt::NoModifier);
            QCoreApplication::sendEvent(&view, &menuKey);
            QCoreApplication::processEvents();
            auto *menu = field->findChild<QObject *>("boundedInputMenu");
            auto *paste = field->findChild<QObject *>("boundedPasteAction");
            if (!menu || !menu->property("opened").toBool() || !paste) { app.exit(8); return; }
            QMetaObject::invokeMethod(paste, "triggered");
            QTimer::singleShot(150, [&]() {
                auto *clipboard = view.rootObject()->findChild<QObject *>("nativeClipboard");
                if (!clipboard || clipboard->property("requests").toInt() != 1) { app.exit(9); return; }
                std::fprintf(stderr, "MENU OK\n"); std::fflush(stderr);
            });
        }
        std::fflush(stderr);
    });
    QTimer timer;
    QObject::connect(&timer, &QTimer::timeout, []() {
        std::fprintf(stderr, "BEAT %lld\n", QDateTime::currentMSecsSinceEpoch());
        std::fflush(stderr);
    });
    timer.start(100);
    return app.exec();
}
