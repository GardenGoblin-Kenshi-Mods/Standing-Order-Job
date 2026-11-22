using Meitou.Game.Ui;
using Meitou.Simulation;

namespace StandingOrderJob;

public sealed class StandHereUi : IUiExtension
{
    readonly Action<string> _log;

    public StandHereUi(Action<string> log) => _log = log;

    public string Id => "standing-order-job";

    public void Register(UiRegistration registration) => registration.AddCharacterMenuVerbs(Verbs);

    IEnumerable<UiMenuVerb> Verbs(UiMenuContext context)
    {
        if (!context.Target.IsPlayer
            || context.Target.Animal
            || !context.Selected.Contains(context.Target.Id))
            return [];

        return [new UiMenuVerb(StandHereTypes.JobName, () => Choose(context))];
    }

    void Choose(UiMenuContext context)
    {
        bool permanent = context.Shift;
        var point = context.Target.Position;
        var recipients = context.Selected.ToArray();
        _log($"menu: Stand Here permanent={permanent} recipients={recipients.Length} "
            + $"point=({point.X:0.##},{point.Y:0.##},{point.Z:0.##}) tick={context.Tick}");
        context.Enqueue(new StandHereCommand(recipients, point, permanent) { Tick = context.Tick });
    }
}
