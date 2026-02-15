'use client';

import { useEffect, useState } from "react"
import { IconUsers, IconCpu, IconChartBar, IconActivity } from "@tabler/icons-react"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  getKiroAccounts,
  getRequestUsageStats,
  getAccounts,
  getQwenAccounts,
  getCodexAccounts,
} from "@/lib/api"

interface ComputedStats {
  totalAccounts: number;
  activeAccounts: number;
  tokensLast24h: number;
  callsLast24h: number;
  totalRequests: number;
  totalTokens: number;
}

export function SectionCards() {
  const [stats, setStats] = useState<ComputedStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadStats = async () => {
      try {
        const now = new Date();
        const last24h = new Date(now.getTime() - 24 * 60 * 60 * 1000);

        const [
          antigravityAccounts,
          kiroAccounts,
          qwenAccounts,
          codexAccounts,
          antigravityStats24h,
          antigravityStatsAll,
          kiroStats24h,
          kiroStatsAll,
          qwenStats24h,
          qwenStatsAll,
          codexStats24h,
          codexStatsAll,
        ] = await Promise.all([
          getAccounts(),
          getKiroAccounts().catch(() => []),
          getQwenAccounts().catch(() => []),
          getCodexAccounts().catch(() => []),
          getRequestUsageStats({ start_date: last24h.toISOString(), config_type: 'antigravity' }).catch(() => null),
          getRequestUsageStats({ config_type: 'antigravity' }).catch(() => null),
          getRequestUsageStats({ start_date: last24h.toISOString(), config_type: 'kiro' }).catch(() => null),
          getRequestUsageStats({ config_type: 'kiro' }).catch(() => null),
          getRequestUsageStats({ start_date: last24h.toISOString(), config_type: 'qwen' }).catch(() => null),
          getRequestUsageStats({ config_type: 'qwen' }).catch(() => null),
          getRequestUsageStats({ start_date: last24h.toISOString(), config_type: 'codex' }).catch(() => null),
          getRequestUsageStats({ config_type: 'codex' }).catch(() => null),
        ]);

        const antigravityCallsLast24h = antigravityStats24h?.total_requests || 0;
        const antigravityTotalRequests = antigravityStatsAll?.total_requests || 0;
        const antigravityTokensLast24h = antigravityStats24h?.total_tokens || 0;
        const antigravityTotalTokens = antigravityStatsAll?.total_tokens || 0;

        const kiroCallsLast24h = kiroStats24h?.total_requests || 0;
        const kiroTotalRequests = kiroStatsAll?.total_requests || 0;
        const kiroTokensLast24h = kiroStats24h?.total_tokens || 0;
        const kiroTotalTokens = kiroStatsAll?.total_tokens || 0;

        const qwenCallsLast24h = qwenStats24h?.total_requests || 0;
        const qwenTotalRequests = qwenStatsAll?.total_requests || 0;
        const qwenTokensLast24h = qwenStats24h?.total_tokens || 0;
        const qwenTotalTokens = qwenStatsAll?.total_tokens || 0;

        const codexCallsLast24h = codexStats24h?.total_requests || 0;
        const codexTotalRequests = codexStatsAll?.total_requests || 0;
        const codexTokensLast24h = codexStats24h?.total_tokens || 0;
        const codexTotalTokens = codexStatsAll?.total_tokens || 0;

        const totalAccounts = antigravityAccounts.length + kiroAccounts.length + qwenAccounts.length + codexAccounts.length;
        const activeAccounts =
          antigravityAccounts.filter((a) => a.status === 1).length +
          kiroAccounts.filter((a) => a.status === 1).length +
          qwenAccounts.filter((a) => a.status === 1).length +
          codexAccounts.filter((a: any) => (a.effective_status ?? a.status) === 1).length;

        setStats({
          totalAccounts,
          activeAccounts,
          tokensLast24h: antigravityTokensLast24h + kiroTokensLast24h + qwenTokensLast24h + codexTokensLast24h,
          callsLast24h: antigravityCallsLast24h + kiroCallsLast24h + qwenCallsLast24h + codexCallsLast24h,
          totalRequests: antigravityTotalRequests + kiroTotalRequests + qwenTotalRequests + codexTotalRequests,
          totalTokens: antigravityTotalTokens + kiroTotalTokens + qwenTotalTokens + codexTotalTokens,
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载数据失败');
      } finally {
        setIsLoading(false);
      }
    };

    loadStats();
  }, []);

  if (isLoading) {
    return (
      <div className="*:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card grid grid-cols-1 gap-4 px-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:shadow-xs lg:px-6 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} className="@container/card">
            <CardHeader>
              <Skeleton className="h-4 w-32 mb-2" />
              <Skeleton className="h-8 w-24" />
            </CardHeader>
            <CardFooter className="flex-col items-start gap-1.5">
              <Skeleton className="h-3 w-40" />
              <Skeleton className="h-3 w-32" />
            </CardFooter>
          </Card>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 lg:px-6">
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-500">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="*:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card grid grid-cols-1 gap-4 px-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:shadow-xs lg:px-6 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>账户总数</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {stats?.totalAccounts || 0}
          </CardTitle>
          <CardAction>
            <Badge variant="outline">
              <IconUsers className="size-4" />
            </Badge>
          </CardAction>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="text-muted-foreground">
            全部渠道合计
          </div>
        </CardFooter>
      </Card>
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>活跃账号数</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {stats?.activeAccounts || 0}
          </CardTitle>
          <CardAction>
            <Badge variant="outline">
              <IconCpu className="size-4" />
            </Badge>
          </CardAction>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="text-muted-foreground">
            全部渠道活跃账号
          </div>
        </CardFooter>
      </Card>
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>24小时 Tokens</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {(stats?.tokensLast24h || 0).toLocaleString()}
          </CardTitle>
          <CardAction>
            <Badge variant="outline">
              <IconChartBar className="size-4" />
            </Badge>
          </CardAction>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            总 Tokens: {(stats?.totalTokens || 0).toLocaleString()}
          </div>
          <div className="text-muted-foreground">全部渠道 Tokens 合计</div>
        </CardFooter>
      </Card>
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>24小时调用量</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {(stats?.callsLast24h || 0).toLocaleString()}
          </CardTitle>
          <CardAction>
            <Badge variant="outline">
              <IconActivity className="size-4" />
            </Badge>
          </CardAction>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            总调用: {(stats?.totalRequests || 0).toLocaleString()} 次
          </div>
          <div className="text-muted-foreground">全部渠道 API 调用合计</div>
        </CardFooter>
      </Card>
    </div>
  )
}
