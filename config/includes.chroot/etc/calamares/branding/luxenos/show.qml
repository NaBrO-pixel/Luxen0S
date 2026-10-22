import QtQuick 2.0
import calamares.slideshow 1.0

Presentation {
    id: presentation

    Slide {
        Image {
            id: background1
            source: "welcome.png"
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop
        }
        Text {
            anchors.centerIn: parent
            text: "Welcome to LuxenOS"
            color: "#e0e6f0"
            font.pointSize: 22
        }
    }

    Slide {
        Text {
            anchors.centerIn: parent
            width: parent.width * 0.7
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            text: "Sway + Waybar for the desktop, Waydroid for Android apps. Aurora Store works out of the box; official Play Store is optional."
            color: "#e0e6f0"
            font.pointSize: 14
        }
    }

    function nextSlide() {
        presentation.goToNextSlide();
    }

    // Presentation has no "timer" property: advance the slides with our own
    // timer while Calamares shows the slideshow (as Debian's show.qml does).
    Timer {
        interval: 10000
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: nextSlide()
    }
}
