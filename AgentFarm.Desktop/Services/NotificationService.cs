using AgentFarm_Desktop.Models;
using Microsoft.Windows.AppNotifications;
using Microsoft.Windows.AppNotifications.Builder;
using System.Collections.ObjectModel;

namespace AgentFarm_Desktop.Services;

public sealed class NotificationService(ObservableCollection<NotificationItem> queue)
{
    public ObservableCollection<NotificationItem> Queue { get; } = queue;

    public void Enqueue(string title, string message, string severity, bool showSystem = false)
    {
        Queue.Insert(0, new NotificationItem
        {
            Title = title,
            Message = message,
            Severity = severity,
            CreatedAt = DateTimeOffset.Now,
        });
        while (Queue.Count > 100)
        {
            Queue.RemoveAt(Queue.Count - 1);
        }

        if (!showSystem)
        {
            return;
        }
        try
        {
            var notification = new AppNotificationBuilder()
                .AddText(title)
                .AddText(message)
                .BuildNotification();
            AppNotificationManager.Default.Show(notification);
        }
        catch
        {
            // The in-app queue remains authoritative when Windows notifications are unavailable.
        }
    }
}
