// Simulates a broken PythonExe that Windows successfully spawns but which does nothing real and
// exits almost instantly -- the exact shape of the confirmed real bug (the Microsoft Store's
// Python alias stub). Point SkyrimNetMultiProxy.ini's PythonExe at this compiled exe to exercise
// LaunchProxy()'s startup-timeout detection without touching the real python.exe or a real game.
int main() {
    return 0;
}
