import ast
import streamlit as st
import json
import pandas as pd
from groq import Groq

# =======================
# CONFIGURATION
# =======================
st.set_page_config(page_title="Rohling - Agile Software Development Process - RPADS", layout="wide")

# =======================
# API KEY MANAGEMENT
# =======================

def get_api_key_from_session():
    return st.session_state.get("groq_api_key", None)

def save_api_key_to_session(api_key):
    st.session_state["groq_api_key"] = api_key

def show_api_key_login():
    st.title("🔐 Configuração de API Key")
    st.markdown("---")
    st.markdown("### É necessário inserir aqui sua API Key do Groq")
    st.markdown("Para usar a aplicação, você precisa fornecer sua chave de API do Groq. "
                "Esta chave será armazenada localmente no seu navegador para futuras sessões.")
    with st.form("api_key_form"):
        api_key_input = st.text_input(
            "🔑 API Key do Groq",
            type="password",
            placeholder="Insira sua API Key aqui...",
            help="Sua API Key do Groq começará com 'gsk_'"
        )
        submitted = st.form_submit_button("✅ Confirmar", use_container_width=True)
        if submitted:
            if api_key_input.strip():
                try:
                    test_client = Groq(api_key=api_key_input)
                    test_client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=10
                    )
                    save_api_key_to_session(api_key_input)
                    st.success("✅ API Key validada com sucesso!")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Erro ao validar API Key: {str(e)}")
            else:
                st.error("❌ Por favor, insira uma API Key válida")

# =======================
# MAIN APP DATA
# =======================

if "stories_collected" not in st.session_state:
    st.session_state.stories_collected = []
if "current_story_index" not in st.session_state:
    st.session_state.current_story_index = 0
if "skip_bn_validation" not in st.session_state:
    st.session_state.skip_bn_validation = False
if "app_stage" not in st.session_state:
    st.session_state.app_stage = None
if "pending_story" not in st.session_state:
    st.session_state.pending_story = None
if "show_decision_screen" not in st.session_state:
    st.session_state.show_decision_screen = False

# =======================
# HELPERS
# =======================

def call_llm(prompt, api_key):
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=3072,
    )
    return response.choices[0].message.content

def _repair_truncated_json(text: str) -> str:
    result = []
    stack = []
    in_string = False
    escape = False

    for ch in text:
        result.append(ch)
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "[{":
            stack.append(ch)
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()

    if in_string and not text.endswith('"'):
        result.append('"')
    while stack:
        open_char = stack.pop()
        result.append(']' if open_char == '[' else '}')

    return "".join(result)

def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass

    repaired = _repair_truncated_json(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Não foi possível parsear a resposta do modelo como JSON após tentativa de reparo: {e}\n"
            f"Resposta recebida:\n{text}\n\nResposta reparada:\n{repaired}"
        )

def format_task_list(task_list):
    if not isinstance(task_list, list):
        return str(task_list)
    formatted = []
    for item in task_list:
        if isinstance(item, str):
            formatted.append(item)
        elif isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("task")
            if title:
                formatted.append(title)
            else:
                formatted.append(json.dumps(item, ensure_ascii=False))
        else:
            formatted.append(str(item))
    return " | ".join(formatted)

def deduplicate_dependencies(deps_list, valid_ids):
    """Remove duplicatas, auto-dependências e IDs inválidos."""
    seen = set()
    clean = []
    for item in deps_list:
        sid = item.get("id")
        if not sid or sid not in valid_ids or sid in seen:
            continue
        seen.add(sid)
        filtered_deps = [
            d for d in item.get("depends_on", [])
            if d in valid_ids and d != sid
        ]
        clean.append({"id": sid, "depends_on": filtered_deps})

    # Garante que toda história apareça, mesmo sem dependências
    for vid in valid_ids:
        if vid not in seen:
            clean.append({"id": vid, "depends_on": []})

    return clean

# =========================
# STEP 1 — STORY POINTS
# =========================

def estimate_sp(data, api_key):
    story_ids = [s["id"] for s in data["stories"]]
    prompt = f"""
Você é um especialista em Scrum experiente.

IDs existentes: {story_ids}

Regras importantes:
- Retorne EXATAMENTE {len(story_ids)} objetos — um por história, na mesma ordem
- NUNCA repita o mesmo ID mais de uma vez
- Use planning poker: 1,3,5,8,13,20
- Considere dependências implícitas
- Evite subestimar integrações (ex: matrícula depende de turma e curso)
- Sistemas administrativos tendem a ser médios (5-8), integrações maiores (8-13)

Backlog:
{json.dumps(data, indent=2)}

Retorne APENAS um JSON válido, sem texto adicional, sem blocos de código:

[
  {{
    "id": "US01",
    "story_points": 5,
    "justification": "curto"
  }}
]
"""
    return extract_json(call_llm(prompt, api_key))

# =========================
# STEP 2 — VN/SP
# =========================

def calculate_ratio(data, sp_data):
    result = []
    for story in data["stories"]:
        sp = next(s["story_points"] for s in sp_data if s["id"] == story["id"])
        ratio = story["business_value"] / sp
        result.append({
            "id": story["id"],
            "vn_sp": round(ratio, 2)
        })
    return result

# =========================
# STEP 3 — DEPENDÊNCIAS
# =========================

def get_dependencies(data, api_key):
    story_ids = [s["id"] for s in data["stories"]]
    prompt = f"""
Analise dependências técnicas entre as histórias abaixo.

IDs existentes: {story_ids}

Regras CRÍTICAS:
- Retorne EXATAMENTE {len(story_ids)} objetos — um por história
- NUNCA repita o mesmo ID mais de uma vez
- Uma história NUNCA pode depender de si mesma
- Use APENAS IDs da lista: {story_ids}
- Se não houver dependência, use "depends_on": []

Regras de negócio:
- Matrícula depende de Turma
- Turma depende de Curso
- Financeiro depende de Matrícula
- Seja conservador (prefira incluir dependência)

Backlog:
{json.dumps(data, indent=2)}

Retorne APENAS um JSON válido, sem texto adicional, sem blocos de código:

[
  {{
    "id": "US01",
    "depends_on": []
  }},
  {{
    "id": "US02",
    "depends_on": ["US01"]
  }}
]
"""
    raw = extract_json(call_llm(prompt, api_key))
    return deduplicate_dependencies(raw, story_ids)

# =========================
# STEP 4 — SPRINTS
# =========================

def plan_sprints(data, sp_data, ratio_data, deps, api_key):
    story_ids = [s["id"] for s in data["stories"]]
    prompt = f"""
Você é um Agile Coach.

IDs existentes: {story_ids}

Dados:

Stories:
{json.dumps(data, indent=2)}

Story Points:
{json.dumps(sp_data, indent=2)}

VN/SP:
{json.dumps(ratio_data, indent=2)}

Dependências:
{json.dumps(deps, indent=2)}

REGRAS:

1. Priorizar maior VN/SP
2. NÃO quebrar dependências
3. Sprint entre 5 e 20 SP
4. Evitar sprint com 1 única história se possível
5. Cada história deve aparecer em EXATAMENTE um sprint
6. Use APENAS os IDs da lista: {story_ids}
7. Ordem lógica: Curso → Turma → Matrícula → Financeiro

Retorne APENAS um JSON válido, sem texto adicional, sem blocos de código:

[
  {{
    "sprint": 1,
    "stories": ["US01"],
    "total_sp": 5
  }}
]
"""
    return extract_json(call_llm(prompt, api_key))

# =========================
# STEP 5 — TASKS
# =========================

def generate_tasks(data, api_key):
    story_ids = [s["id"] for s in data["stories"]]
    prompt = f"""
Quebre cada história em tarefas técnicas REALISTAS.

IDs existentes: {story_ids}

Regras:
- Retorne EXATAMENTE {len(story_ids)} objetos — um por história
- NUNCA repita o mesmo story_id mais de uma vez
- Use APENAS os IDs da lista: {story_ids}
- Backend + Frontend + DB + Testes
- Use linguagem simples
- Máximo 6 tarefas por história

Backlog:
{json.dumps(data, indent=2)}

Retorne APENAS um JSON válido, sem texto adicional, sem blocos de código:

[
  {{
    "story_id": "US01",
    "tasks": ["Criar endpoint REST", "Criar tela de cadastro", "Criar tabela no banco", "Escrever testes unitários"]
  }}
]
"""
    return extract_json(call_llm(prompt, api_key))

# =========================
# STEP 0 — VALIDATE BUSINESS VALUE
# =========================

def validate_business_value(story_title, story_description, proposed_value, api_key):
    prompt = f"""
Você é um especialista em análise de valor de negócio em projetos de software.

História: {story_title}
Descrição: {story_description}
Valor de Negócio Proposto: {proposed_value}

Analise BREVEMENTE (máximo 5 linhas) se este valor faz sentido para esta história.
Se achar apropriado, sugira um novo valor.

Formato:
- Análise breve (2-3 linhas)
- Sugestão: [mesmo valor ou outro valor de 1 a 300]

Exemplo de resposta:
Este valor está adequado pois a história é de alta relevância. A funcionalidade de matrícula é crítica.
Sugestão: Manter 200
"""
    try:
        return call_llm(prompt, api_key)
    except Exception as e:
        return f"❌ Erro ao analisar: {str(e)}"

# =========================
# MAIN APP
# =========================

api_key = get_api_key_from_session()

if not api_key:
    show_api_key_login()
else:
    with st.sidebar:
        st.markdown("---")
        if st.button("🔄 Alterar API Key"):
            del st.session_state["groq_api_key"]
            st.rerun()

    st.title("🚀 Rohling - Agile Software Development Process(RPADS) - Planejamento de Sprints")
    st.markdown("### Etapa 1️⃣: Coleta de Histórias")

    if st.session_state.stories_collected:
        st.info(f"✅ {len(st.session_state.stories_collected)} história(s) coletada(s)")
        with st.expander("📋 Ver histórias coletadas"):
            for i, story in enumerate(st.session_state.stories_collected, 1):
                st.write(f"**{i}. {story['title']}**")
                st.write(f"   Descrição: {story['description']}")
                st.write(f"   Valor de Negócio: {story['business_value']}")

    st.markdown("---")

    # DECISION SCREEN
    if st.session_state.show_decision_screen and st.session_state.pending_story:
        st.markdown("### ✅ História Adicionada com Sucesso!")
        st.write(f"**Título:** {st.session_state.pending_story['title']}")
        st.write(f"**Valor de Negócio:** {st.session_state.pending_story['business_value']}")
        st.markdown("---")
        st.markdown("### Próximos Passos")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("➕ Incluir Nova História de Usuário", use_container_width=True, key="add_another_story"):
                st.session_state.show_decision_screen = False
                st.session_state.pending_story = None
                st.rerun()
        with col2:
            if st.button("🚀 Finalizar e Ir para Estimar Story Points", use_container_width=True, key="finish_and_estimate"):
                st.session_state.show_decision_screen = False
                st.session_state.pending_story = None
                st.session_state.app_stage = "story_points"
                st.rerun()

    # FORM SCREEN
    elif not st.session_state.show_decision_screen:
        st.markdown("#### Adicionar Nova História")

        with st.form("story_form", clear_on_submit=True):
            story_description = st.text_area(
                "📄 Descrição da História de Usuário",
                placeholder="Ex: Como usuário, quero poder cadastrar cursos no sistema...",
                help="Descreva a história completa do usuário",
                height=100
            )
            business_value = st.slider(
                "💰 Valor de Negócio Inicial",
                min_value=1,
                max_value=300,
                value=100,
                help="Escala de 1 (baixo) a 300 (alto valor)"
            )
            submitted = st.form_submit_button("📤 Enviar para Análise", use_container_width=True)

        if submitted and story_description.strip():
            story_title = story_description.strip()[:50] + "..." if len(story_description.strip()) > 50 else story_description.strip()
            st.session_state.pending_story = {
                "title": story_title,
                "description": story_description,
                "business_value": business_value,
                "analysis": None
            }
            st.rerun()

    # ANALYSIS SCREEN
    if st.session_state.pending_story and not st.session_state.show_decision_screen:
        story = st.session_state.pending_story
        st.markdown("---")
        st.markdown("### 🤖 Análise do Valor de Negócio")

        with st.spinner("Analisando valor de negócio..."):
            analysis = validate_business_value(story["title"], story["description"], story["business_value"], api_key)

        st.session_state.pending_story["analysis"] = analysis
        st.info(analysis)
        st.markdown("### 👤 Sua Decisão")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Manter Valor", key="keep_value", use_container_width=True):
                new_story = {
                    "id": f"US{str(len(st.session_state.stories_collected) + 1).zfill(2)}",
                    "title": story["title"],
                    "description": story["description"],
                    "business_value": story["business_value"]
                }
                st.session_state.stories_collected.append(new_story)
                st.session_state.show_decision_screen = True
                st.rerun()
        with col2:
            new_bn = st.number_input(
                "📝 Novo Valor (1-300)",
                min_value=1,
                max_value=300,
                value=story["business_value"],
                key="new_bn_input"
            )
            if st.button("✏️ Alterar Valor", key="change_value", use_container_width=True):
                new_story = {
                    "id": f"US{str(len(st.session_state.stories_collected) + 1).zfill(2)}",
                    "title": story["title"],
                    "description": story["description"],
                    "business_value": new_bn
                }
                st.session_state.stories_collected.append(new_story)
                st.session_state.pending_story["business_value"] = new_bn
                st.session_state.show_decision_screen = True
                st.rerun()

    # STORY POINTS STAGE
    if st.session_state.get("app_stage") == "story_points":
        st.markdown("---")
        st.markdown("### Etapa 2️⃣: Estimando Story Points")
        st.markdown(f"**Total de histórias:** {len(st.session_state.stories_collected)}")

        data = {"stories": st.session_state.stories_collected}

        if st.button("⚙️ Processar Planejamento", use_container_width=True):
            try:
                st.subheader("1️⃣ Estimando Story Points...")
                sp = estimate_sp(data, api_key)
                st.json(sp)

                st.subheader("2️⃣ Calculando VN/SP...")
                ratio = calculate_ratio(data, sp)
                st.json(ratio)

                st.subheader("3️⃣ Dependências...")
                deps = get_dependencies(data, api_key)
                st.json(deps)

                st.subheader("4️⃣ Planejamento de Sprints...")
                sprints = plan_sprints(data, sp, ratio, deps, api_key)
                st.json(sprints)

                st.subheader("5️⃣ Tarefas...")
                tasks = generate_tasks(data, api_key)
                st.json(tasks)

                st.subheader("📊 Resultado Final")

                rows = []
                for sprint in sprints:
                    for story_id in sprint["stories"]:
                        story = next((s for s in data["stories"] if s["id"] == story_id), None)
                        if not story:
                            continue
                        sp_item = next((s for s in sp if s["id"] == story_id), None)
                        sp_value = sp_item["story_points"] if sp_item else "—"
                        task_item = next((t for t in tasks if t["story_id"] == story_id), None)
                        task_list = task_item["tasks"] if task_item else []

                        rows.append({
                            "Sprint": sprint["sprint"],
                            "Story": story_id,
                            "Título": story["title"],
                            "SP": sp_value,
                            "Tarefas": format_task_list(task_list)
                        })

                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True)

                st.markdown("---")
                if st.button("🔄 Iniciar Novo Planejamento", use_container_width=True):
                    st.session_state.stories_collected = []
                    st.session_state.app_stage = None
                    st.session_state.skip_bn_validation = False
                    st.session_state.pending_story = None
                    st.session_state.show_decision_screen = False
                    st.rerun()

            except json.JSONDecodeError as e:
                st.error(f"❌ Erro ao parsear dados: {e}")
            except Exception as e:
                st.error(f"❌ Erro ao gerar planejamento: {e}")