'use client';

import { useEffect, useRef, useState } from 'react';
import {
  getRequestLogBody,
  getRequestUsageLogs,
  getRequestUsageStats,
  getUiDefaultChannels,
  type RequestUsageLogItem,
  type RequestUsageStats,
} from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer';
import { MorphingSquare } from '@/components/ui/morphing-square';
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '@/components/ui/pagination';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import Toaster, { type ToasterRef } from '@/components/ui/toast';
import { Gemini, OpenAI, Qwen } from '@lobehub/icons';

type ConfigType =
  | 'antigravity'
  | 'kiro'
  | 'qwen'
  | 'codex'
  | 'gemini-cli'
  | 'zai-tts'
  | 'zai-image';

const PAGE_SIZE = 50;

export default function AnalyticsPage() {
  const toasterRef = useRef<ToasterRef>(null);

  const [requestStats, setRequestStats] = useState<RequestUsageStats | null>(null);
  const [requestLogs, setRequestLogs] = useState<RequestUsageLogItem[]>([]);
  const [requestCurrentPage, setRequestCurrentPage] = useState(1);
  const [requestTotalRecords, setRequestTotalRecords] = useState(0);

  const [selectedLogId, setSelectedLogId] = useState<number | null>(null);
  const [requestHeaders, setRequestHeaders] = useState<string | null>(null);
  const [requestBody, setRequestBody] = useState<string | null>(null);
  const [isLoadingBody, setIsLoadingBody] = useState(false);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  const [activeTab, setActiveTab] = useState<ConfigType>('antigravity');
  const [isTabInitialized, setIsTabInitialized] = useState(false);
  const [isLoadingRequests, setIsLoadingRequests] = useState(true);

  useEffect(() => {
    const init = async () => {
      try {
        const settings = await getUiDefaultChannels();
        if (settings.usage_default_channel) {
          setActiveTab(settings.usage_default_channel);
        }
      } catch {
      } finally {
        setIsTabInitialized(true);
      }
    };

    init();
  }, []);

  useEffect(() => {
    if (!isTabInitialized) return;

    const run = async () => {
      setIsLoadingRequests(true);
      try {
        const offset = (requestCurrentPage - 1) * PAGE_SIZE;
        const configType = activeTab;
        const [statsData, logsData] = await Promise.all([
          getRequestUsageStats({ config_type: configType }),
          getRequestUsageLogs({ config_type: configType, limit: PAGE_SIZE, offset }),
        ]);
        setRequestStats(statsData);
        setRequestLogs(logsData.logs);
        setRequestTotalRecords(logsData.pagination.total);
      } catch (err) {
        toasterRef.current?.show({
          title: '加载失败',
          message: err instanceof Error ? err.message : '加载数据失败',
          variant: 'error',
          position: 'top-right',
        });
        setRequestStats(null);
        setRequestLogs([]);
        setRequestTotalRecords(0);
      } finally {
        setIsLoadingRequests(false);
      }
    };

    run();
  }, [isTabInitialized, activeTab, requestCurrentPage]);

  const handleRequestPageChange = (page: number) => {
    setRequestCurrentPage(page);
  };

  const handleViewRequestBody = async (logId: number) => {
    setSelectedLogId(logId);
    setIsDrawerOpen(true);
    setIsLoadingBody(true);
    setRequestHeaders(null);
    setRequestBody(null);

    try {
      const result = await getRequestLogBody(logId);

      if (result.request_headers) {
        try {
          const parsedHeaders = JSON.parse(result.request_headers);
          setRequestHeaders(JSON.stringify(parsedHeaders, null, 2));
        } catch {
          setRequestHeaders(result.request_headers);
        }
      } else {
        setRequestHeaders(null);
      }

      if (result.request_body) {
        try {
          const parsed = JSON.parse(result.request_body);
          setRequestBody(JSON.stringify(parsed, null, 2));
        } catch {
          setRequestBody(result.request_body);
        }
      } else {
        setRequestBody(null);
      }
    } catch (err) {
      toasterRef.current?.show({
        title: '获取失败',
        message: err instanceof Error ? err.message : '获取请求体失败',
        variant: 'error',
        position: 'top-right',
      });
      setIsDrawerOpen(false);
    } finally {
      setIsLoadingBody(false);
    }
  };

  const requestTotalPages = Math.ceil(requestTotalRecords / PAGE_SIZE);

  const getModelDisplayName = (model: string) => {
    const modelNames: Record<string, string> = {
      'gemini-2.5-flash-lite': 'Gemini 2.5 Flash Lite',
      'claude-sonnet-4-5-thinking': 'Claude Sonnet 4.5 (Thinking)',
      'claude-opus-4-6-thinking': 'Claude Opus 4.6 (Thinking)',
      'claude-opus-4-5-thinking': 'Claude Opus 4.5 (Thinking)',
      'gemini-2.5-flash-image': 'Gemini 2.5 Flash Image',
      'gemini-2.5-flash-thinking': 'Gemini 2.5 Flash (Thinking)',
      'gemini-2.5-flash': 'Gemini 2.5 Flash',
      'gemini-2.5-pro': 'Gemini 2.5 Pro',
      'gpt-oss-120b-medium': 'GPT OSS 120B (Medium)',
      'gemini-3-pro-image': 'Gemini 3 Pro Image',
      'gemini-3-pro-high': 'Gemini 3 Pro (High)',
      'gemini-3-pro-low': 'Gemini 3 Pro (Low)',
      'claude-sonnet-4-5': 'Claude Sonnet 4.5',
      'rev19-uic3-1p': 'Rev19 UIC3 1P',
      'chat_20706': 'Chat 20706',
      'chat_23310': 'Chat 23310',
    };
    return modelNames[model] || model;
  };

  const requestProviderLabel =
    activeTab === 'antigravity'
      ? 'Antigravity'
      : activeTab === 'kiro'
        ? 'Kiro'
        : activeTab === 'codex'
          ? 'Codex'
          : activeTab === 'gemini-cli'
            ? 'GeminiCLI'
            : activeTab === 'zai-tts'
              ? 'ZAI TTS'
              : activeTab === 'zai-image'
                ? 'ZAI Image'
                : 'Qwen';

  const isFirstLoadForTab = requestLogs.length === 0 && !requestStats;

  if (isLoadingRequests && isFirstLoadForTab) {
    return (
      <div className="flex flex-col gap-4 py-4 md:gap-6 md:py-6">
        <div className="px-4 lg:px-6">
          <div className="flex items-center justify-center min-h-screen">
            <MorphingSquare message="加载中..." />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 py-4 md:gap-6 md:py-6">
      <div className="px-4 lg:px-6">
        <div className="flex items-center justify-between mb-6">
          <div></div>
          <Select
            value={activeTab}
            onValueChange={(value: ConfigType) => {
              setActiveTab(value);
              setRequestCurrentPage(1);
            }}
          >
            <SelectTrigger className="w-[160px] h-9">
              <SelectValue>
                {activeTab === 'antigravity' ? (
                  <span className="flex items-center gap-2">
                    <img src="/antigravity-logo.png" alt="" className="size-4 rounded" />
                    Antigravity
                  </span>
                ) : activeTab === 'kiro' ? (
                  <span className="flex items-center gap-2">
                    <img src="/kiro.png" alt="" className="size-4 rounded" />
                    Kiro
                  </span>
                ) : activeTab === 'qwen' ? (
                  <span className="flex items-center gap-2">
                    <Qwen className="size-4" />
                    Qwen
                  </span>
                ) : activeTab === 'zai-tts' ? (
                  <span className="flex items-center gap-2">
                    <OpenAI className="size-4" />
                    ZAI TTS
                  </span>
                ) : activeTab === 'zai-image' ? (
                  <span className="flex items-center gap-2">
                    <OpenAI className="size-4" />
                    ZAI Image
                  </span>
                ) : activeTab === 'gemini-cli' ? (
                  <span className="flex items-center gap-2">
                    <Gemini.Color className="size-4" />
                    GeminiCLI
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    <OpenAI className="size-4" />
                    Codex
                  </span>
                )}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="antigravity">
                <span className="flex items-center gap-2">
                  <img src="/antigravity-logo.png" alt="" className="size-4 rounded" />
                  Antigravity
                </span>
              </SelectItem>
              <SelectItem value="kiro">
                <span className="flex items-center gap-2">
                  <img src="/kiro.png" alt="" className="size-4 rounded" />
                  Kiro
                </span>
              </SelectItem>
              <SelectItem value="qwen">
                <span className="flex items-center gap-2">
                  <Qwen className="size-4" />
                  Qwen
                </span>
              </SelectItem>
              <SelectItem value="zai-tts">
                <span className="flex items-center gap-2">
                  <OpenAI className="size-4" />
                  ZAI TTS
                </span>
              </SelectItem>
              <SelectItem value="zai-image">
                <span className="flex items-center gap-2">
                  <OpenAI className="size-4" />
                  ZAI Image
                </span>
              </SelectItem>
              <SelectItem value="gemini-cli">
                <span className="flex items-center gap-2">
                  <Gemini.Color className="size-4" />
                  GeminiCLI
                </span>
              </SelectItem>
              <SelectItem value="codex">
                <span className="flex items-center gap-2">
                  <OpenAI className="size-4" />
                  Codex
                </span>
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <Toaster ref={toasterRef} defaultPosition="top-right" />

        <Card className="mb-6">
          <CardHeader>
            <CardTitle>请求统计</CardTitle>
            <CardDescription>
              统计本系统记录的 {requestProviderLabel} 调用（成功与失败都会记录），可在下方请求记录中点击「查看」查看请求头/请求体
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoadingRequests ? (
              <div className="flex items-center justify-center h-40">
                <MorphingSquare message="加载中..." />
              </div>
            ) : requestStats ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">总请求数</p>
                  <p className="text-2xl font-bold">{(requestStats.total_requests || 0).toLocaleString()}</p>
                </div>

                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">{activeTab === 'zai-image' ? '总次数' : '总 Tokens'}</p>
                  {activeTab === 'zai-image' ? (
                    <p className="text-2xl font-bold">
                      {(requestStats.total_quota_consumed || 0).toLocaleString()}
                    </p>
                  ) : (
                    (() => {
                      const totalTokens = requestStats.total_tokens || 0;
                      const inputTokens = requestStats.input_tokens || 0;
                      const cachedTokens = requestStats.cached_tokens || 0;
                      const outputTokens = requestStats.output_tokens || 0;
                      const uncachedInputTokens = Math.max(inputTokens - cachedTokens, 0);
                      const sum = uncachedInputTokens + cachedTokens + outputTokens;

                      return (
                        <div className="flex items-center gap-2">
                          <span className="text-2xl font-bold">{totalTokens.toLocaleString()}</span>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Badge
                                variant="secondary"
                                className="h-5 px-1.5 text-[10px] leading-4 cursor-default select-none"
                              >
                                明细
                              </Badge>
                            </TooltipTrigger>
                            <TooltipContent sideOffset={6} className="max-w-[260px]">
                              <div className="space-y-1">
                                <div className="font-mono">输入 {uncachedInputTokens.toLocaleString()}</div>
                                <div className="font-mono">缓存 {cachedTokens.toLocaleString()}</div>
                                <div className="font-mono">输出 {outputTokens.toLocaleString()}</div>
                                <div className="pt-1 border-t border-background/20 font-mono">合计 {sum.toLocaleString()}</div>
                              </div>
                            </TooltipContent>
                          </Tooltip>
                        </div>
                      );
                    })()
                  )}
                </div>

                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">成功 / 失败</p>
                  <p className="text-2xl font-bold">
                    {(requestStats.success_requests || 0).toLocaleString()} / {(requestStats.failed_requests || 0).toLocaleString()}
                  </p>
                </div>

                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">平均耗时</p>
                  <p className="text-2xl font-bold">{Math.round(requestStats.avg_duration_ms || 0).toLocaleString()}ms</p>
                </div>
              </div>
            ) : (
              <div className="text-center py-12 text-muted-foreground">
                <p className="text-sm">暂无统计数据</p>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>请求记录</CardTitle>
            <CardDescription>共 {requestTotalRecords} 条请求记录</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoadingRequests ? (
              <div className="flex items-center justify-center h-40">
                <MorphingSquare message="加载中..." />
              </div>
            ) : requestLogs.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <p className="text-lg mb-2">暂无请求记录</p>
                <p className="text-sm">
                  {activeTab === 'zai-image'
                    ? `先用 ${requestProviderLabel} 生成一张图吧！`
                    : `先用 ${requestProviderLabel} 发起一次对话吧！`}
                </p>
              </div>
            ) : (
              <>
                <div className="overflow-x-auto -mx-6 px-6 md:mx-0 md:px-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="min-w-[90px]">状态</TableHead>
                        <TableHead className="min-w-[160px]">模型</TableHead>
                        {activeTab === 'zai-tts' && (
                          <>
                            <TableHead className="min-w-[140px]">音色ID</TableHead>
                            <TableHead className="min-w-[140px]">账号ID</TableHead>
                          </>
                        )}
                        {activeTab === 'zai-image' ? (
                          <TableHead className="min-w-[110px]">次数</TableHead>
                        ) : (
                          <>
                            <TableHead className="min-w-[110px]">Input</TableHead>
                            <TableHead className="min-w-[110px]">Output</TableHead>
                            <TableHead className="min-w-[110px]">Total</TableHead>
                          </>
                        )}
                        <TableHead className="min-w-[100px]">耗时</TableHead>
                        <TableHead className="min-w-[160px]">时间</TableHead>
                        <TableHead className="min-w-[240px]">错误</TableHead>
                        <TableHead className="min-w-[80px]">操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {requestLogs.map((log) => (
                        <TableRow key={log.id}>
                          <TableCell>
                            <Badge variant={log.success ? 'secondary' : 'destructive'}>
                              {log.success ? '成功' : '失败'}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1">
                              <Badge variant="outline" className="whitespace-nowrap w-fit">
                                {getModelDisplayName(log.model_name || 'unknown')}
                              </Badge>
                              <div className="text-xs text-muted-foreground font-mono whitespace-nowrap">
                                {log.model_name || '-'}
                              </div>
                            </div>
                          </TableCell>

                          {activeTab === 'zai-tts' && (
                            <>
                              <TableCell className="font-mono text-sm whitespace-nowrap">
                                {log.tts_voice_id || '-'}
                              </TableCell>
                              <TableCell className="font-mono text-sm whitespace-nowrap">
                                {log.tts_account_id || '-'}
                              </TableCell>
                            </>
                          )}

                          {activeTab === 'zai-image' ? (
                            <TableCell className="font-mono text-sm whitespace-nowrap">
                              {(log.quota_consumed || 0).toLocaleString()}
                            </TableCell>
                          ) : (
                            <>
                              <TableCell className="font-mono text-sm whitespace-nowrap">
                                {(log.input_tokens || 0).toLocaleString()}
                              </TableCell>
                              <TableCell className="font-mono text-sm whitespace-nowrap">
                                {(log.output_tokens || 0).toLocaleString()}
                              </TableCell>
                              <TableCell className="font-mono text-sm whitespace-nowrap">
                                {(log.total_tokens || 0).toLocaleString()}
                              </TableCell>
                            </>
                          )}

                          <TableCell className="font-mono text-sm whitespace-nowrap">
                            {(log.duration_ms || 0).toLocaleString()}ms
                          </TableCell>
                          <TableCell className="text-sm whitespace-nowrap">
                            {log.created_at ? new Date(log.created_at).toLocaleString('zh-CN') : '-'}
                          </TableCell>
                          <TableCell className="text-sm">
                            <div className="max-w-[360px] truncate" title={log.error_message || ''}>
                              {log.error_message || '-'}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => handleViewRequestBody(log.id)}
                            >
                              查看
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                {requestTotalPages > 1 && (
                  <div className="mt-4 flex justify-center">
                    <Pagination>
                      <PaginationContent>
                        <PaginationItem>
                          <PaginationPrevious
                            onClick={() => requestCurrentPage > 1 && handleRequestPageChange(requestCurrentPage - 1)}
                            className={requestCurrentPage === 1 ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                          />
                        </PaginationItem>

                        {Array.from({ length: Math.min(requestTotalPages, 5) }, (_, i) => {
                          let pageNum;
                          if (requestTotalPages <= 5) {
                            pageNum = i + 1;
                          } else if (requestCurrentPage <= 3) {
                            pageNum = i + 1;
                          } else if (requestCurrentPage >= requestTotalPages - 2) {
                            pageNum = requestTotalPages - 4 + i;
                          } else {
                            pageNum = requestCurrentPage - 2 + i;
                          }

                          return (
                            <PaginationItem key={pageNum}>
                              <PaginationLink
                                onClick={() => handleRequestPageChange(pageNum)}
                                isActive={requestCurrentPage === pageNum}
                                className="cursor-pointer"
                              >
                                {pageNum}
                              </PaginationLink>
                            </PaginationItem>
                          );
                        })}

                        {requestTotalPages > 5 && requestCurrentPage < requestTotalPages - 2 && (
                          <PaginationItem>
                            <PaginationEllipsis />
                          </PaginationItem>
                        )}

                        <PaginationItem>
                          <PaginationNext
                            onClick={() => requestCurrentPage < requestTotalPages && handleRequestPageChange(requestCurrentPage + 1)}
                            className={requestCurrentPage === requestTotalPages ? 'pointer-events-none opacity-50' : 'cursor-pointer'}
                          />
                        </PaginationItem>
                      </PaginationContent>
                    </Pagination>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Drawer open={isDrawerOpen} onOpenChange={setIsDrawerOpen} direction="right">
        <DrawerContent className="w-full sm:max-w-2xl">
          <DrawerHeader>
            <DrawerTitle>请求详情</DrawerTitle>
            <DrawerDescription>日志 ID: {selectedLogId}</DrawerDescription>
          </DrawerHeader>
          <div className="flex-1 overflow-auto px-4">
            {isLoadingBody ? (
              <div className="flex items-center justify-center h-40">
                <MorphingSquare message="加载中..." />
              </div>
            ) : (
              <Tabs defaultValue="body" className="w-full">
                <TabsList className="mb-2">
                  <TabsTrigger value="headers">请求头</TabsTrigger>
                  <TabsTrigger value="body">请求体</TabsTrigger>
                </TabsList>

                <TabsContent value="headers">
                  {requestHeaders ? (
                    <pre className="text-xs font-mono bg-muted p-4 rounded-md overflow-auto max-h-[70vh] whitespace-pre-wrap break-all">
                      {requestHeaders}
                    </pre>
                  ) : (
                    <div className="text-center py-12 text-muted-foreground">
                      <p className="text-sm">无请求头数据</p>
                    </div>
                  )}
                </TabsContent>

                <TabsContent value="body">
                  {requestBody ? (
                    <pre className="text-xs font-mono bg-muted p-4 rounded-md overflow-auto max-h-[70vh] whitespace-pre-wrap break-all">
                      {requestBody}
                    </pre>
                  ) : (
                    <div className="text-center py-12 text-muted-foreground">
                      <p className="text-sm">无请求体数据</p>
                    </div>
                  )}
                </TabsContent>
              </Tabs>
            )}
          </div>
          <DrawerFooter>
            <DrawerClose asChild>
              <Button variant="outline">关闭</Button>
            </DrawerClose>
          </DrawerFooter>
        </DrawerContent>
      </Drawer>
    </div>
  );
}
