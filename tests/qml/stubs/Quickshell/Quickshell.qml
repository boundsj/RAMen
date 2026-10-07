pragma Singleton
import QtQuick
QtObject { function env(name) { return name === "HOME" ? "/nonexistent-home" : "" } }
