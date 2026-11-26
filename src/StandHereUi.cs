using Meitou.Game.Ui;
using Meitou.Simulation;

namespace StandingOrderJob;

/// <summary>Exposes the Stand Here verb on selected player character hold-right-click menus.</summary>
public sealed class StandHereUi : IUiExtension
{
    readonly Action<string> _log;

    /// <summary>Initializes the UI extension with the mod's opt-in debug logger.</summary>
    public StandHereUi(Action<string> log) => _log = log;

    /// <summary>Implements <see cref="IUiExtension.Id"/>.</summary>
    public string Id => "standing-order-job";

    /// <summary>Implements <see cref="IUiExtension.Register"/>. Registers the Stand Here verb on character hold-right-click menus.</summary>
    public void Register(UiRegistration registration) => registration.AddCharacterMenuVerbs(Verbs);

    /// <summary>Yields the Stand Here menu verb only when targeting a selected, non-animal player character.</summary>
    IEnumerable<UiMenuVerb> Verbs(UiMenuContext context)
    {
        bool isPlayerTarget = context.Target.IsPlayer;
        bool isAnimalTarget = context.Target.Animal;
        bool isTargetSelected = context.Selected.Contains(context.Target.Id);
        if (!isPlayerTarget || isAnimalTarget || !isTargetSelected)
            return [];

        return [new UiMenuVerb(StandHereTypes.JobName, () => Choose(context))];
    }

    /// <summary>Enqueues a <see cref="StandHereCommand"/> targeting the clicked character's position (permanent job when Shift is held, one-shot move otherwise).</summary>
    void Choose(UiMenuContext context)
    {
        bool isPermanent = context.Shift;
        var standPoint = context.Target.Position;
        var recipients = context.Selected.ToArray();
        _log($"menu: Stand Here permanent={isPermanent} recipients={recipients.Length} "
            + $"point={StandHereTypes.FormatPoint(standPoint)} tick={context.Tick}");
        context.Enqueue(new StandHereCommand(recipients, standPoint, isPermanent) { Tick = context.Tick });
    }
}
