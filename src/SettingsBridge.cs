namespace StandingOrderJob;

/// <summary>Thread-safely bridges Mods-page settings into the serial simulation <c>Inputs</c> phase.</summary>
public sealed class SettingsBridge(StandHereSettings settings)
{
    readonly Lock _sync = new();
    int _pendingMaxReturnDistanceMeters = Clamp(settings.MaxReturnDistanceMeters);
    bool _isDirty = true;

    /// <summary>Captures current Mods-page setting values and marks them dirty for the next simulation <c>Inputs</c> tick.</summary>
    public void MarkDirty()
    {
        lock (_sync)
        {
            _pendingMaxReturnDistanceMeters = Clamp(settings.MaxReturnDistanceMeters);
            _isDirty = true;
        }
    }

    /// <summary>Atomically drains pending setting updates when dirty and outputs the latest value.</summary>
    public bool TryTake(out int maxReturnDistanceMeters)
    {
        lock (_sync)
        {
            maxReturnDistanceMeters = _pendingMaxReturnDistanceMeters;
            if (!_isDirty)
                return false;

            _isDirty = false;
            return true;
        }
    }

    /// <summary>Returns the most recently staged maximum return distance in metres.</summary>
    public int CurrentMaxReturnDistanceMeters
    {
        get
        {
            lock (_sync)
                return _pendingMaxReturnDistanceMeters;
        }
    }

    /// <summary>Clamps raw setting values to the valid return distance range.</summary>
    static int Clamp(int value) =>
        Math.Clamp(value, StandHereSettings.MinDistanceMeters, StandHereSettings.MaxDistanceMeters);
}
