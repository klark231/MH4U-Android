#include "input_map.h"

#include <cmath>
#include <cstdio>

using namespace Port::InputMap;

static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++fails; } } while (0)

static Source Key(int code, const char* dev = "") { return {Source::Kind::Key, code, dev}; }

int main() {
    // defaults: Android's bottom face button (96) is the 3DS B, the right one (97) the A
    Engine e;
    e.LoadDefaults();
    e.OnKey("padA", 97, true);
    CHECK(e.Poll().buttons == A);
    e.OnKey("padA", 97, false);
    CHECK(e.Poll().buttons == 0);

    // a hat and a trigger axis act as digital buttons, with hysteresis
    e.OnAxis("padA", 15, -1.0f);
    CHECK(e.Poll().buttons == Left);
    e.OnAxis("padA", 15, 0.0f);
    e.OnAxis("padA", 17, 0.6f);
    CHECK(e.Poll().buttons == ZL);
    e.OnAxis("padA", 17, 0.4f);              // below press, above release: still held
    CHECK(e.Poll().buttons == ZL);
    e.OnAxis("padA", 17, 0.2f);
    CHECK(e.Poll().buttons == 0);

    // sticks: deadzone, inversion of Android's Y, strongest device wins
    e.OnAxis("padA", 0, 0.05f);
    CHECK(e.Poll().sticks[CircleX] == 0.0f);
    e.OnAxis("padA", 0, 1.0f);
    e.OnAxis("padA", 1, -1.0f);              // pushed up
    auto st = e.Poll();
    CHECK(std::fabs(st.sticks[CircleX] - 1.0f) < 1e-5 && std::fabs(st.sticks[CircleY] - 1.0f) < 1e-5);
    e.OnAxis("padB", 0, -0.5f);
    CHECK(e.Poll().sticks[CircleX] > 0.9f);
    e.ReleaseAll();

    // combo: one key presses several 3DS buttons
    Engine c;
    c.AddBinding({{Key(200)}, L | R | A, {}});
    c.OnKey("kbd", 200, true);
    CHECK(c.Poll().buttons == (L | R | A));
    c.OnKey("kbd", 200, false);

    // chord: Select+Start gives Home; the parts alone still work, but not along with the chord
    c.Clear();
    c.AddBinding({{Key(1)}, Select, {}});
    c.AddBinding({{Key(2)}, Start, {}});
    c.AddBinding({{Key(1), Key(2)}, Home, {}});
    c.OnKey("p", 1, true);
    CHECK(c.Poll().buttons == Select);
    c.OnKey("p", 2, true);
    CHECK(c.Poll().buttons == Home);
    c.OnKey("p", 1, false);
    CHECK(c.Poll().buttons == Start);
    c.ReleaseAll();

    // device binding: a source for one device ignores the same key on another
    c.Clear();
    c.AddBinding({{Key(96, "pad1")}, B, {}});
    c.AddBinding({{Key(96, "pad2")}, Y, {}});
    c.OnKey("pad2", 96, true);
    CHECK(c.Poll().buttons == Y);
    c.OnKey("pad1", 96, true);
    CHECK(c.Poll().buttons == (Y | B));
    c.OnDeviceRemoved("pad1");               // unplugged while held: released
    CHECK(c.Poll().buttons == Y);
    c.ReleaseAll();

    // keys moving a stick (keyboard)
    c.Clear();
    Binding w{{Key(51)}, 0, {}};
    w.push[CircleY] = 1.0f;
    c.AddBinding(w);
    c.OnKey("kbd", 51, true);
    CHECK(c.Poll().sticks[CircleY] == 1.0f);
    c.ReleaseAll();

    // learning a combo: hold two keys from two devices, let go, they are one chord
    Engine l;
    l.StartLearn();
    CHECK(l.Learning());
    l.OnKey("pad1", 102, true);
    CHECK(l.Poll().buttons == 0);            // no output meanwhile
    l.OnKey("kbd", 40, true);
    CHECK(!l.TakeLearned());
    l.OnKey("pad1", 102, false);
    CHECK(l.Learning());                     // one still held
    l.OnKey("kbd", 40, false);
    auto learned = l.TakeLearned();
    CHECK(learned && learned->size() == 2 && !l.Learning());
    CHECK(learned && (*learned)[0] == Key(102, "pad1") && (*learned)[1] == Key(40, "kbd"));
    l.AddBinding({*learned, X | Y, {}});
    l.OnKey("pad1", 102, true);
    CHECK(l.Poll().buttons == 0);
    l.OnKey("kbd", 40, true);
    CHECK(l.Poll().buttons == (X | Y));

    // text form round trip, including axis sources and a comment
    Engine s;
    s.LoadDefaults();
    s.AddBinding({{Key(1, "abc"), {Source::Kind::AxisNeg, 15, ""}}, A | B | ZR, {}});
    const std::string text = s.Serialize();
    Engine t;
    std::vector<std::string> errors;
    CHECK(t.Parse(text, &errors) && errors.empty());
    CHECK(t.Serialize() == text);
    CHECK(t.Bindings().size() == s.Bindings().size());
    t.OnKey("abc", 1, true);
    t.OnAxis("x", 15, -1.0f);
    CHECK(t.Poll().buttons == (A | B | ZR)); // Left (the hat alone) is part of the chord: it stays quiet
    // bad lines are skipped and reported
    Engine u;
    CHECK(!u.Parse("bind = Q <= key:1\nbind = A <= key:x\n# fine\nbind = A <= key:7\n", &errors));
    CHECK(errors.size() >= 2);
    CHECK(u.Bindings().size() == 1);         // lines it can't read are skipped, the rest is used
    if (!fails) std::printf("input_map ok\n");
    return fails;
}
