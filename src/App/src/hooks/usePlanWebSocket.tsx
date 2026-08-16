/**
 * usePlanWebSocket — extracts all WebSocket event subscriptions
 * from PlanPage into one reusable hook.
 *
 * Dispatches Redux actions for each event type so PlanPage no longer
 * needs 7+ useEffect blocks for WebSocket handling.
 */
import React, { useEffect } from 'react';
import webSocketService from '@/store/WebSocketService';
import { PlanDataService } from '@/store/PlanDataService';
import { useAppDispatch, useAppSelector } from '@/store/hooks';
import {
    setShowProcessingPlanSpinner,
    setShowApprovalButtons,
    setReloadLeftList,
    selectPlanData,
    selectContinueWithWebsocketFlow,
    selectPlanApproved,
    selectShowProcessingPlanSpinner,
    setShowTimeoutDialog,
    setTimeoutMessage,
    approvalRequestReceived,
    planCompletedFinal,
    planFailedFinal,
} from '@/store/slices/planSlice';
import {
    setSubmittingChatDisableInput,
    setClarificationMessage,
    addAgentMessage,
} from '@/store/slices/chatSlice';
import {
    appendToStreamingBuffer,
    setShowBufferingText,
    addStreamingMessage,
    selectStreamingMessageBuffer,
} from '@/store/slices/streamingSlice';
import { setWsConnected } from '@/store/slices/appSlice';
import { setSelectedTeam } from '@/store/slices/teamSlice';
import {
    WebsocketMessageType,
    MPlanData,
    AgentMessageData,
    AgentMessageType,
    AgentType,
    PlanStatus,
    ParsedUserClarification,
    StreamMessage,
    ProcessedPlanData,
    StreamingPlanUpdate,
    ConnectionStatusPayload,
} from '@/models';
import { APIService } from '@/api/apiService';
import { ToastIntent } from '@/components/toast/InlineToaster';
import { formatElapsedTime, toEpochMs } from '@/utils';

const apiService = new APIService();

interface UsePlanWebSocketProps {
    planId: string | undefined;
    scrollToBottom: () => void;
    scrollToFinalResult: () => void;
    formatErrorMessage: (content: string) => string;
    showToast: (content: React.ReactNode, intent?: ToastIntent, options?: { dismissible?: boolean; timeoutMs?: number | null }) => number;
}

/**
 * Creates an AgentMessageResponse and persists it, then optionally reloads the task list.
 */
function persistAgentMessage(
    agentMessageData: AgentMessageData,
    planData: ProcessedPlanData | null,
    dispatch: ReturnType<typeof useAppDispatch>,
    isFinal = false,
    streamingMessage = '',
) {
    if (!planData?.plan) return;

    const agentMessageResponse = PlanDataService.createAgentMessageResponse(
        agentMessageData,
        planData,
        isFinal,
        streamingMessage,
    );
    apiService
        .sendAgentMessage(agentMessageResponse)
        .then(() => {
            if (isFinal) {
                setTimeout(() => dispatch(setReloadLeftList(true)), 1000);
            }
        })
        .catch(() => {
            if (isFinal) {
                setTimeout(() => dispatch(setReloadLeftList(true)), 1000);
            }
        });
}

/**
 * Permissive view of a websocket callback argument.
 *
 * `on()` hands back a StreamMessage, but these handlers also read properties
 * defensively from several payload shapes the backend has used. Spelling those out
 * keeps the reads visible and checked, where `any` hid them entirely. Intersecting
 * with StreamMessage narrows its `data?: unknown` to the shape below.
 */
type WsPayload = {
    parsedData?: MPlanData;
    rawData?: unknown;
    content?: string;
    message?: string;
    data?: {
        content?: string;
        message?: string;
        parsedData?: MPlanData;
        data?: { content?: string };
        [key: string]: unknown;
    };
};

export function usePlanWebSocket({
    planId,
    scrollToBottom,
    scrollToFinalResult,
    formatErrorMessage,
    showToast,
}: UsePlanWebSocketProps) {
    const dispatch = useAppDispatch();
    const planData = useAppSelector(selectPlanData);
    const planApproved = useAppSelector(selectPlanApproved);
    const showProcessingPlanSpinner = useAppSelector(selectShowProcessingPlanSpinner);
    const continueWithWebsocketFlow = useAppSelector(selectContinueWithWebsocketFlow);
    const streamingMessageBuffer = useAppSelector(selectStreamingMessageBuffer);
    const processingStartedAtRef = React.useRef<number | null>(null);

    // Coalesce high-frequency streaming tokens into one flush per animation frame
    // to avoid a synchronous re-render per token freezing the UI on fast streams.
    const streamingChunkQueueRef = React.useRef<string[]>([]);
    const streamingFlushHandleRef = React.useRef<number | null>(null);

    useEffect(() => {
        if (showProcessingPlanSpinner) {
            if (processingStartedAtRef.current === null) {
                processingStartedAtRef.current = Date.now();
            }
        } else {
            processingStartedAtRef.current = null;
        }
    }, [showProcessingPlanSpinner]);

    // ── PLAN_APPROVAL_REQUEST ─────────────────────────────────────
    useEffect(() => {
        const unsub = webSocketService.on(
            WebsocketMessageType.PLAN_APPROVAL_REQUEST,
            (wsMessage: StreamMessage) => {
                    const approvalRequest = wsMessage as StreamMessage & WsPayload;
                let mPlanData: MPlanData | null = null;
                if (approvalRequest.parsedData) {
                    mPlanData = approvalRequest.parsedData;
                } else if (approvalRequest.data?.parsedData) {
                    mPlanData = approvalRequest.data.parsedData;
                } else if (approvalRequest.data && typeof approvalRequest.data === 'object') {
                    mPlanData = approvalRequest.data as unknown as MPlanData;
                } else if (approvalRequest.rawData) {
                    mPlanData = PlanDataService.parsePlanApprovalRequest(approvalRequest.rawData);
                } else {
                    mPlanData = PlanDataService.parsePlanApprovalRequest(approvalRequest);
                }
                if (mPlanData) {
                    /* P0: single compound action replaces 4 separate dispatches */
                    dispatch(approvalRequestReceived(mPlanData));
                    scrollToBottom();
                }
            },
        );
        return unsub;
    }, [dispatch, scrollToBottom]);

    // ── AGENT_MESSAGE_STREAMING ───────────────────────────────────
    useEffect(() => {
        const flushStreamingChunks = () => {
            streamingFlushHandleRef.current = null;
            const chunks = streamingChunkQueueRef.current;
            if (chunks.length === 0) return;
            streamingChunkQueueRef.current = [];
            dispatch(setShowBufferingText(true));
            dispatch(appendToStreamingBuffer(chunks.join('')));
        };

        const unsub = webSocketService.on(
            WebsocketMessageType.AGENT_MESSAGE_STREAMING,
            (wsMessage: StreamMessage) => {
                    const msg = wsMessage as StreamMessage & WsPayload;
                const line = PlanDataService.simplifyHumanClarification(msg.data?.content || msg.content || '');
                streamingChunkQueueRef.current.push(line);
                if (streamingFlushHandleRef.current === null) {
                    streamingFlushHandleRef.current = requestAnimationFrame(flushStreamingChunks);
                }
            },
        );
        return () => {
            unsub();
            // Cancel pending frame and flush leftovers so no streamed text is lost
            if (streamingFlushHandleRef.current !== null) {
                cancelAnimationFrame(streamingFlushHandleRef.current);
                streamingFlushHandleRef.current = null;
            }
            if (streamingChunkQueueRef.current.length > 0) {
                const remaining = streamingChunkQueueRef.current.join('');
                streamingChunkQueueRef.current = [];
                dispatch(appendToStreamingBuffer(remaining));
            }
        };
    }, [dispatch]);

    // ── USER_CLARIFICATION_REQUEST ────────────────────────────────
    useEffect(() => {
        const unsub = webSocketService.on(
            WebsocketMessageType.USER_CLARIFICATION_REQUEST,
            (wsMessage: StreamMessage) => {
                    const msg = wsMessage as StreamMessage & WsPayload;
                if (!msg) return;
                const agentMessageData: AgentMessageData = {
                    agent: AgentType.GROUP_CHAT_MANAGER,
                    agent_type: AgentMessageType.AI_AGENT,
                    timestamp: toEpochMs(msg.timestamp as string | number) ?? Date.now(),
                    steps: [],
                    next_steps: [],
                    content: (msg.data?.question as string) || '',
                    raw_data: (msg.data as unknown as string) || '',
                };
                dispatch(setClarificationMessage(msg.data as unknown as ParsedUserClarification));
                dispatch(addAgentMessage(agentMessageData));
                dispatch(setShowBufferingText(false));
                dispatch(setShowProcessingPlanSpinner(false));
                processingStartedAtRef.current = null;
                dispatch(setSubmittingChatDisableInput(false));
                scrollToBottom();
                persistAgentMessage(agentMessageData, planData, dispatch);
            },
        );
        return unsub;
    }, [dispatch, scrollToBottom, planData]);

    // ── AGENT_TOOL_MESSAGE (currently no-op, kept for future) ─────
    useEffect(() => {
        const unsub = webSocketService.on(WebsocketMessageType.AGENT_TOOL_MESSAGE, () => { /* intentional no-op */ });
        return unsub;
    }, []);

    // ── FINAL_RESULT_MESSAGE ──────────────────────────────────────
    useEffect(() => {
        const unsub = webSocketService.on(
            WebsocketMessageType.FINAL_RESULT_MESSAGE,
            (wsMessage: StreamMessage) => {
                    const finalMessage = wsMessage as StreamMessage & WsPayload;
                if (!finalMessage) return;
                const completionElapsedSeconds = processingStartedAtRef.current
                    ? Math.max(Math.round((Date.now() - processingStartedAtRef.current) / 1000), 0)
                    : null;
                const completionTimeLine = completionElapsedSeconds !== null
                    ? `\n\n**Total completion time: ${formatElapsedTime(completionElapsedSeconds)}**`
                    : '';
                const messageStatus = finalMessage?.data?.status;

                if (messageStatus === PlanStatus.COMPLETED) {
                    const agentMessageData: AgentMessageData = {
                        agent: AgentType.GROUP_CHAT_MANAGER,
                        agent_type: AgentMessageType.AI_AGENT,
                        timestamp: Date.now(),
                        steps: [],
                        next_steps: [],
                        content: (finalMessage.data?.content || '') + completionTimeLine,
                        raw_data: finalMessage as unknown as string,
                    };
                    dispatch(setShowBufferingText(true));
                    dispatch(addAgentMessage(agentMessageData));
                    dispatch(setSelectedTeam(planData?.team || null));
                    /* P0: single compound action replaces setShowProcessingPlanSpinner(false) + markPlanCompleted() */
                    dispatch(planCompletedFinal());
                    processingStartedAtRef.current = null;
                    scrollToFinalResult();
                    webSocketService.disconnect();
                    persistAgentMessage(agentMessageData, planData, dispatch, true, streamingMessageBuffer);
                } else if (messageStatus === 'error') {
                    // Safety net: handle error status sent as FINAL_RESULT_MESSAGE
                    const errorContent = finalMessage.data?.content || 'An unexpected error occurred. Please try again later.';
                    const errorAgent: AgentMessageData = {
                        agent: 'system',
                        agent_type: AgentMessageType.SYSTEM_AGENT,
                        timestamp: Date.now(),
                        steps: [],
                        next_steps: [],
                        content: formatErrorMessage(errorContent),
                        raw_data: finalMessage as unknown as string,
                    };
                    dispatch(addAgentMessage(errorAgent));
                    dispatch(planFailedFinal());
                    dispatch(setShowBufferingText(false));
                    dispatch(setSubmittingChatDisableInput(true));
                    scrollToBottom();
                    showToast(errorContent, 'error');
                    webSocketService.disconnect();
                } else {
                    // Any other terminal status (e.g. "terminated"): clear the spinner
                    // so the UI doesn't hang after the answer has already arrived.
                    const content = finalMessage.data?.content;
                    if (content) {
                        const terminalMessage: AgentMessageData = {
                            agent: AgentType.GROUP_CHAT_MANAGER,
                            agent_type: AgentMessageType.AI_AGENT,
                            timestamp: Date.now(),
                            steps: [],
                            next_steps: [],
                            content,
                            raw_data: finalMessage as unknown as string,
                        };
                        dispatch(addAgentMessage(terminalMessage));
                    }
                    dispatch(setShowBufferingText(false));
                    dispatch(setShowProcessingPlanSpinner(false));
                    processingStartedAtRef.current = null;
                    scrollToBottom();
                    webSocketService.disconnect();
                }
            },
        );
        return unsub;
    }, [dispatch, scrollToBottom, scrollToFinalResult, planData, streamingMessageBuffer, formatErrorMessage, showToast]);

    // ── ERROR_MESSAGE ─────────────────────────────────────────────
    useEffect(() => {
        const unsub = webSocketService.on(
            WebsocketMessageType.ERROR_MESSAGE,
            (wsMessage: StreamMessage) => {
                    const errorMessage = wsMessage as StreamMessage & WsPayload;
                let errorContent = 'An unexpected error occurred. Please try again later.';
                if (errorMessage?.data?.data?.content) {
                    const c = errorMessage.data.data.content.trim();
                    if (c.length > 0) errorContent = c;
                } else if (errorMessage?.data?.content) {
                    const c = errorMessage.data.content.trim();
                    if (c.length > 0) errorContent = c;
                } else if (errorMessage?.content) {
                    const c = errorMessage.content.trim();
                    if (c.length > 0) errorContent = c;
                } else if (typeof (errorMessage as unknown) === 'string') {
                    const c = (errorMessage as unknown as string).trim();
                    if (c.length > 0) errorContent = c;
                }
                const errorAgent: AgentMessageData = {
                    agent: 'system',
                    agent_type: AgentMessageType.SYSTEM_AGENT,
                    timestamp: Date.now(),
                    steps: [],
                    next_steps: [],
                    content: formatErrorMessage(errorContent),
                    raw_data: (errorMessage as unknown as string) || '',
                };
                dispatch(addAgentMessage(errorAgent));
                dispatch(planFailedFinal());
                processingStartedAtRef.current = null;
                dispatch(setShowBufferingText(false));
                dispatch(setSubmittingChatDisableInput(true));
                scrollToBottom();
                showToast(errorContent, 'error');
                webSocketService.disconnect();
            },
        );
        return unsub;
    }, [dispatch, scrollToBottom, showToast, formatErrorMessage]);

    // ── TIMEOUT_NOTIFICATION ──────────────────────────────────────
    useEffect(() => {
        const unsub = webSocketService.on(
            WebsocketMessageType.TIMEOUT_NOTIFICATION,
            (wsMessage: StreamMessage) => {
                    const msg = wsMessage as StreamMessage & WsPayload;
                const message = msg?.data?.message || msg?.message ||
                    'Session timed out. Please go back to home and try again.';
                dispatch(setTimeoutMessage(message));
                dispatch(setShowTimeoutDialog(true));
                dispatch(setShowProcessingPlanSpinner(false));
                dispatch(setShowApprovalButtons(false));
                webSocketService.disconnect();
            },
        );
        return unsub;
    }, [dispatch]);

    // ── AGENT_MESSAGE ─────────────────────────────────────────────
    useEffect(() => {
        const unsub = webSocketService.on(
            WebsocketMessageType.AGENT_MESSAGE,
            (wsMessage: StreamMessage) => {
                    const agentMessage = wsMessage as StreamMessage & WsPayload;
                // Only process agent messages after the user has approved the plan
                if (!planApproved) return;

                const agentMessageData = agentMessage.data as unknown as AgentMessageData;
                if (agentMessageData) {
                    agentMessageData.content = PlanDataService.simplifyHumanClarification(
                        agentMessageData?.content,
                    );
                    dispatch(addAgentMessage(agentMessageData));
                    dispatch(setShowProcessingPlanSpinner(true));
                    scrollToBottom();
                    persistAgentMessage(agentMessageData, planData, dispatch);
                }
            },
        );
        return unsub;
    }, [dispatch, scrollToBottom, planData, planApproved]);

    // ── WebSocket connect / disconnect lifecycle ──────────────────
    useEffect(() => {
        if (!planId || !continueWithWebsocketFlow) return;

        const connectWebSocket = async () => {
            try {
                await webSocketService.connect(planId);
            } catch {
                console.log('WebSocket connection failed, continuing without real-time updates');
            }
        };
        connectWebSocket();

        const handleConnectionChange = (connected: boolean) => {
            dispatch(setWsConnected(connected));
        };

        const handleStreamingMessage = (message: StreamMessage) => {
            const data = message.data as StreamingPlanUpdate | undefined;
            if (data?.plan_id) {
                dispatch(addStreamingMessage(data));
            }
        };

        const unsubConnection = webSocketService.on('connection_status', (msg) =>
            handleConnectionChange((msg.data as ConnectionStatusPayload | undefined)?.connected || false),
        );
        const unsubStreaming = webSocketService.on(
            WebsocketMessageType.AGENT_MESSAGE,
            handleStreamingMessage,
        );
        const unsubApproval = webSocketService.on(WebsocketMessageType.PLAN_APPROVAL_RESPONSE, () => { /* intentional no-op */ });
        const unsubApprovalReq = webSocketService.on(WebsocketMessageType.PLAN_APPROVAL_REQUEST, () => { /* intentional no-op */ });

        return () => {
            unsubConnection();
            unsubStreaming();
            unsubApproval();
            unsubApprovalReq();
            webSocketService.disconnect();
        };
    }, [dispatch, planId, continueWithWebsocketFlow]);
}

export default usePlanWebSocket;