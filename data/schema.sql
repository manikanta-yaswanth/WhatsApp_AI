-- Additive bootstrap: compatible with the original Alembic 0001 tables.
-- UUIDs are assigned by Python; no extension or superuser permissions required.

CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID NOT NULL,
    query TEXT NOT NULL,
    intent VARCHAR(40),
    answer TEXT,
    status VARCHAR(20) NOT NULL,
    tool_calls JSONB DEFAULT '[]' NOT NULL,
    iterations INTEGER DEFAULT '0' NOT NULL,
    retry_count INTEGER DEFAULT '0' NOT NULL,
    prompt_tokens INTEGER DEFAULT '0' NOT NULL,
    completion_tokens INTEGER DEFAULT '0' NOT NULL,
    latency_ms INTEGER,
    judge_score FLOAT,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS contacts (
    id UUID NOT NULL,
    whatsapp_id VARCHAR(255) NOT NULL,
    phone_number VARCHAR(32),
    contact_name VARCHAR(255),
    is_group BOOLEAN DEFAULT 'false' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (whatsapp_id)
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id UUID NOT NULL,
    status VARCHAR(20) NOT NULL,
    dataset VARCHAR(255),
    total_questions INTEGER DEFAULT '0' NOT NULL,
    metrics JSONB DEFAULT '{}' NOT NULL,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id UUID NOT NULL,
    status VARCHAR(30) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    extraction_method VARCHAR(20),
    contacts_found INTEGER DEFAULT '0' NOT NULL,
    messages_found INTEGER DEFAULT '0' NOT NULL,
    contacts_saved INTEGER DEFAULT '0' NOT NULL,
    messages_saved INTEGER DEFAULT '0' NOT NULL,
    messages_pruned INTEGER DEFAULT '0' NOT NULL,
    invalid_records INTEGER DEFAULT '0' NOT NULL,
    duration_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID NOT NULL,
    contact_id UUID NOT NULL,
    unread_count INTEGER DEFAULT '0' NOT NULL,
    last_message_at TIMESTAMP WITH TIME ZONE,
    last_scraped_at TIMESTAMP WITH TIME ZONE,
    category VARCHAR(40),
    category_confidence FLOAT,
    category_reason TEXT,
    action VARCHAR(20),
    classified_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    UNIQUE (contact_id),
    FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluations (
    id UUID NOT NULL,
    evaluation_run_id UUID,
    agent_run_id UUID,
    question TEXT NOT NULL,
    expected TEXT,
    expected_tools JSONB,
    tools_used JSONB DEFAULT '[]' NOT NULL,
    answer TEXT,
    correctness FLOAT,
    relevance FLOAT,
    groundedness FLOAT,
    completeness FLOAT,
    hallucination FLOAT,
    tool_accuracy FLOAT,
    schema_compliant BOOLEAN DEFAULT 'true' NOT NULL,
    final_score FLOAT,
    reasoning TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(evaluation_run_id) REFERENCES evaluation_runs (id) ON DELETE CASCADE,
    FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID NOT NULL,
    contact_id UUID NOT NULL,
    whatsapp_message_id VARCHAR(255) NOT NULL,
    sender_type VARCHAR(20) NOT NULL,
    sender_name VARCHAR(255),
    message_type VARCHAR(30) DEFAULT 'text' NOT NULL,
    message_text TEXT,
    message_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
    UNIQUE (whatsapp_message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_contact_time ON messages (contact_id, message_timestamp DESC);
