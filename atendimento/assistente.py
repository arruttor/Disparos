"""Assistente de atendimento: IA (Claude, Gemini…) + ferramentas que chamam as funções já testadas do sistema.

Provedores: Anthropic usa o SDK oficial (`_turno_anthropic`); Gemini e outros compatíveis
com OpenAI usam o SDK `openai` no endpoint do provedor (`_turno_compativel`).

Fluxo de um turno (`responder`):
  histórico da conversa (só texto, do banco) → API → ferramentas → … → texto final.

Sobre o histórico: os turnos anteriores são reconstruídos do banco **só com texto**.
Dentro do turno, a resposta da API é devolvida completa (com os blocos de thinking),
sem editar nada — o modelo exige histórico "append-only" para os blocos de thinking.
"""

import copy
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import anthropic
import openai
from django.utils import timezone

from core.models import Dentista, Procedimento
from integracoes import ia
from integracoes.models import Configuracao

from . import agendamentos
from .agenda import buscar_horarios_livres
from .models import Agendamento, Mensagem

logger = logging.getLogger("clinica.assistente")

MAX_ITERACOES = 8  # chamadas à API por turno (cada ferramenta usada conta uma ida e volta)
JANELA_HISTORICO = timedelta(hours=72)  # conversa mais antiga que isso não entra como contexto
MAX_MENSAGENS_HISTORICO = 40
ESFORCO = "medium"  # Claude Opus 5.5: padrão é medium; atendimento por chat não precisa de mais
BETAS = ["server-side-fallback-2026-07-01"]

DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

MENSAGEM_FALHA = (
    "Recebemos sua mensagem! Em instantes alguém da nossa equipe vai te responder. 🙂"
)


class ErroAssistente(Exception):
    pass


@dataclass
class Resultado:
    texto: str = ""
    transferiu: bool = False
    acoes: list = field(default_factory=list)  # log legível do que o assistente fez


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _prompt_fixo(config, clinica):
    """Parte estável do prompt (vai para o cache). Nada que mude a cada mensagem aqui."""
    nome = config.assistente_nome.strip()
    apresentacao = f"{nome}, assistente virtual" if nome else "o assistente virtual"
    if config.assistente_tratamento == Configuracao.Tratamento.SENHOR:
        tom = ("Trate o paciente por “o senhor” / “a senhora” (use o nome quando souber). "
               "Seja educado e acolhedor, num tom profissional.")
    else:
        tom = "Trate o paciente por “você”. Seja simpático, acolhedor e direto, como uma boa recepcionista."

    procedimentos = "\n".join(
        f"- id {p.pk}: {p.nome} ({p.duracao_minutos} min){' — URGÊNCIA' if p.urgencia else ''}"
        f"{': ' + p.descricao if p.descricao else ''}"
        for p in Procedimento.objects.filter(clinica=clinica, ativo=True).order_by("nome")
    )
    extras = config.assistente_instrucoes.strip() or "(nenhuma informação extra cadastrada)"

    return f"""Você é {apresentacao} da {clinica.nome}, uma clínica odontológica. Você atende pacientes pelo WhatsApp: tira dúvidas e marca, confirma, remarca e cancela consultas na agenda real da clínica, usando as ferramentas.

# Como falar
- {tom}
- Português do Brasil. Mensagens curtas, como no WhatsApp: no máximo 2 ou 3 frases por mensagem, sem listas longas. Para destacar, use *asterisco* (negrito do WhatsApp); não use títulos, tabelas nem markdown.
- Se perguntarem, diga com naturalidade que você é um assistente virtual da clínica e que a equipe acompanha as conversas.
- Datas e horários sempre no formato brasileiro (ex.: “terça, 07/10, às 14h30”), horário de Brasília.

# Regras que você nunca quebra
1. **Nunca informe preços nem faça orçamento.** Valores são passados somente na avaliação com o dentista. Se perguntarem preço, explique isso com gentileza e ofereça marcar uma avaliação.
2. **Nunca invente horários.** Só ofereça horários que vieram da ferramenta buscar_horarios_livres nesta conversa. Ofereça 2 ou 3 opções por vez, não a lista inteira.
3. **Antes de marcar, remarcar ou cancelar, confirme com o paciente** o procedimento, o dia e o horário — e só execute depois do “sim”. Para marcar, você precisa do nome do paciente; se não souber, pergunte e salve com salvar_nome_paciente.
4. **Não faça diagnóstico nem receite nada.** Você pode identificar qual procedimento faz sentido marcar a partir do relato (dor, limpeza, aparelho…). Na dúvida, marque uma Avaliação.
5. **Urgências** (dor forte, inchaço, sangramento, dente quebrado com dor, trauma): demonstre cuidado e ofereça o primeiro horário disponível do procedimento de urgência. Se houver sinal de gravidade — inchaço que se espalha pelo rosto ou pescoço, dificuldade para respirar ou engolir, febre alta, sangramento que não para — oriente a procurar um pronto-socorro imediatamente e transfira para a equipe.
6. **Transfira para a equipe (transferir_para_humano)** quando: o paciente pedir para falar com uma pessoa; houver reclamação ou insatisfação; o assunto for pagamento, reembolso, convênio, laudos ou documentos; for uma dúvida clínica específica; ou você não souber responder. Depois de transferir, avise o paciente que alguém da equipe vai continuar o atendimento por aqui.
7. Só trate de assuntos da clínica. A clínica atende **somente particular** (não aceita convênio).
8. Use só as consultas que a ferramenta listar_consultas_do_paciente retornar; nunca exponha dados de outros pacientes.
9. Você **não consegue abrir** imagens, áudios, vídeos nem documentos — eles chegam só como uma descrição (ex.: "🎤 Áudio", "📷 Imagem"). Diga isso com gentileza e peça para o paciente escrever. Se parecer ser um exame, receita ou documento clínico, transfira para a equipe.
10. A clínica manda na véspera um pedido de confirmação ("Você confirma? Responda SIM…"). Um "sim" simples é confirmado automaticamente, sem você. Se o paciente responder outra coisa a esse pedido (quer mudar o horário, não vai poder ir, tem dúvida), ajude: remarque com buscar_horarios_livres + remarcar_consulta, cancele com cancelar_consulta ou confirme com confirmar_consulta se ele confirmar com outras palavras.

# Procedimentos (use o id nas ferramentas)
{procedimentos}

# Informações da clínica
{extras}"""


def _prompt_do_turno(conversa, agora):
    """Parte que muda: data/hora, unidade, dentistas e o paciente desta conversa."""
    unidade = conversa.unidade
    local = timezone.localtime(agora)
    dentistas = []
    for d in Dentista.objects.filter(unidade=unidade, ativo=True).prefetch_related("procedimentos", "horarios"):
        procs = ", ".join(str(p.pk) for p in d.procedimentos.all()) or "todos"
        dias = sorted({h.dia_semana for h in d.horarios.all()})
        atende = ", ".join(DIAS_SEMANA[i] for i in dias) or "sem horário cadastrado"
        dentistas.append(f"- id {d.pk}: {d.nome} — procedimentos (ids): {procs} — atende: {atende}")
    paciente = conversa.paciente
    proximas = (
        Agendamento.objects.filter(paciente=paciente, fim__gte=agora)
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("procedimento", "dentista")
        .order_by("inicio")
    )
    consultas = "\n".join(
        f"- consulta {a.pk}: {a.procedimento.nome} com {a.dentista.nome}, {_formatar(a.inicio)} ({a.get_status_display()})"
        for a in proximas
    ) or "- nenhuma"
    endereco = f" — {unidade.endereco}" if unidade.endereco else ""
    return f"""# Agora
Hoje é {DIAS_SEMANA[local.weekday()]}, {local:%d/%m/%Y}, {local:%H:%M} (horário de Brasília).

# Unidade deste atendimento
{unidade.nome}{f' ({unidade.cidade})' if unidade.cidade else ''}{endereco}

Dentistas desta unidade:
{chr(10).join(dentistas) or '- nenhum cadastrado (não é possível marcar; transfira para a equipe)'}

# Paciente
Nome: {paciente.nome or '(ainda não informado)'}
WhatsApp: +{paciente.telefone}
Próximas consultas:
{consultas}"""


def _formatar(dt):
    local = timezone.localtime(dt)
    return f"{DIAS_SEMANA[local.weekday()]}, {local:%d/%m} às {local:%H:%M}"


# ---------------------------------------------------------------------------
# Ferramentas
# ---------------------------------------------------------------------------


def _ferramenta(nome, descricao, propriedades, obrigatorias=None):
    return {
        "name": nome,
        "description": descricao,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": propriedades,
            "required": obrigatorias if obrigatorias is not None else list(propriedades),
            "additionalProperties": False,
        },
    }


FERRAMENTAS = [
    _ferramenta(
        "buscar_horarios_livres",
        "Busca horários livres REAIS na agenda para um procedimento, por até 5 dias a partir de uma data. "
        "Use sempre antes de oferecer horários. dentista_id pode ser null para considerar todos os dentistas da unidade.",
        {
            "procedimento_id": {"type": "integer"},
            "data_inicial": {"type": "string", "description": "AAAA-MM-DD"},
            "dentista_id": {"type": ["integer", "null"]},
        },
    ),
    _ferramenta(
        "marcar_consulta",
        "Marca a consulta na agenda. Só use depois que o paciente confirmou procedimento, dentista, dia e horário.",
        {
            "procedimento_id": {"type": "integer"},
            "dentista_id": {"type": "integer"},
            "inicio": {"type": "string", "description": "AAAA-MM-DDTHH:MM, horário de Brasília, exatamente como veio de buscar_horarios_livres"},
            "observacoes": {"type": "string", "description": "Resumo curto do que o paciente relatou (pode ser vazio)"},
        },
    ),
    _ferramenta(
        "listar_consultas_do_paciente",
        "Lista as próximas consultas deste paciente (com o id de cada uma).",
        {},
    ),
    _ferramenta(
        "confirmar_consulta",
        "Marca a consulta como confirmada pelo paciente.",
        {"consulta_id": {"type": "integer"}},
    ),
    _ferramenta(
        "remarcar_consulta",
        "Move a consulta para outro horário livre (obtido de buscar_horarios_livres). Só use após o paciente confirmar.",
        {"consulta_id": {"type": "integer"}, "novo_inicio": {"type": "string", "description": "AAAA-MM-DDTHH:MM"}},
    ),
    _ferramenta(
        "cancelar_consulta",
        "Cancela a consulta. Só use após o paciente confirmar que quer cancelar.",
        {"consulta_id": {"type": "integer"}, "motivo": {"type": "string"}},
    ),
    _ferramenta(
        "salvar_nome_paciente",
        "Salva o nome completo do paciente no cadastro.",
        {"nome": {"type": "string"}},
    ),
    _ferramenta(
        "transferir_para_humano",
        "Passa a conversa para a equipe da clínica (o assistente para de responder nesta conversa).",
        {
            "motivo": {"type": "string", "description": "Por que está transferindo, em poucas palavras"},
            "resumo": {"type": "string", "description": "Resumo da conversa para a equipe continuar sem perguntar tudo de novo"},
        },
    ),
]


def _ler_inicio(valor):
    try:
        dt = datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ErroAssistente(f"Horário inválido: {valor!r}. Use AAAA-MM-DDTHH:MM.")
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _consulta_do_paciente(conversa, consulta_id):
    ag = (
        Agendamento.objects.filter(pk=consulta_id, paciente=conversa.paciente)
        .select_related("dentista__unidade", "procedimento", "paciente")
        .first()
    )
    if ag is None:
        raise ErroAssistente("Consulta não encontrada para este paciente.")
    return ag


def _dentistas_validos(conversa, procedimento, dentista_id):
    qs = Dentista.objects.filter(unidade=conversa.unidade, ativo=True)
    if dentista_id:
        qs = qs.filter(pk=dentista_id)
    return [d for d in qs.prefetch_related("procedimentos")
            if not d.procedimentos.exists() or procedimento in d.procedimentos.all()]


def executar_ferramenta(conversa, nome, entrada, resultado):
    """Executa a ferramenta e devolve texto para o modelo. Erros de regra viram texto (is_error)."""
    clinica = conversa.unidade.clinica

    if nome == "buscar_horarios_livres":
        procedimento = Procedimento.objects.filter(pk=entrada["procedimento_id"], clinica=clinica, ativo=True).first()
        if not procedimento:
            raise ErroAssistente("Procedimento não encontrado.")
        try:
            data_inicial = date.fromisoformat(entrada["data_inicial"])
        except ValueError:
            raise ErroAssistente("data_inicial inválida; use AAAA-MM-DD.")
        data_inicial = max(data_inicial, timezone.localdate())
        dentistas = _dentistas_validos(conversa, procedimento, entrada.get("dentista_id"))
        if not dentistas:
            raise ErroAssistente("Nenhum dentista desta unidade faz esse procedimento.")
        linhas = []
        for d in dentistas:
            dias = buscar_horarios_livres(d, procedimento, data_inicial, dias=5)
            for dia, horarios in dias.items():
                horas = ", ".join(f"{h:%H:%M}" for h in horarios)
                linhas.append(f"{d.nome} (dentista_id {d.pk}) — {DIAS_SEMANA[dia.weekday()]} {dia:%d/%m} ({dia.isoformat()}): {horas}")
        resultado.acoes.append(f"buscou horários de {procedimento.nome} a partir de {data_inicial:%d/%m}")
        if not linhas:
            return (f"Nenhum horário livre para {procedimento.nome} nos 5 dias a partir de {data_inicial:%d/%m}. "
                    "Tente buscar a partir de uma data posterior.")
        return f"Horários livres para {procedimento.nome} ({procedimento.duracao_minutos} min):\n" + "\n".join(linhas)

    if nome == "marcar_consulta":
        procedimento = Procedimento.objects.filter(pk=entrada["procedimento_id"], clinica=clinica, ativo=True).first()
        dentista = Dentista.objects.filter(pk=entrada["dentista_id"], unidade=conversa.unidade, ativo=True).first()
        if not procedimento or not dentista:
            raise ErroAssistente("Procedimento ou dentista inválido para esta unidade.")
        if not conversa.paciente.nome:
            raise ErroAssistente("Pergunte o nome do paciente e salve com salvar_nome_paciente antes de marcar.")
        ag = agendamentos.marcar(
            conversa.paciente, dentista, procedimento, _ler_inicio(entrada["inicio"]),
            origem=Agendamento.Origem.ASSISTENTE, observacoes=entrada.get("observacoes", "")[:500],
        )
        resultado.acoes.append(f"marcou consulta {ag.pk} ({procedimento.nome}, {_formatar(ag.inicio)})")
        return f"Consulta marcada (id {ag.pk}): {procedimento.nome} com {dentista.nome}, {_formatar(ag.inicio)}. Status: aguardando confirmação."

    if nome == "listar_consultas_do_paciente":
        proximas = (
            Agendamento.objects.filter(paciente=conversa.paciente, fim__gte=timezone.now())
            .exclude(status=Agendamento.Status.CANCELADO).select_related("procedimento", "dentista").order_by("inicio")
        )
        if not proximas:
            return "O paciente não tem consultas futuras."
        return "\n".join(
            f"consulta_id {a.pk}: {a.procedimento.nome} com {a.dentista.nome}, {_formatar(a.inicio)} — {a.get_status_display()}"
            for a in proximas
        )

    if nome == "confirmar_consulta":
        ag = agendamentos.confirmar(_consulta_do_paciente(conversa, entrada["consulta_id"]))
        resultado.acoes.append(f"confirmou consulta {ag.pk}")
        return f"Consulta {ag.pk} confirmada ({_formatar(ag.inicio)})."

    if nome == "remarcar_consulta":
        ag = agendamentos.remarcar(_consulta_do_paciente(conversa, entrada["consulta_id"]), _ler_inicio(entrada["novo_inicio"]))
        resultado.acoes.append(f"remarcou consulta {ag.pk} para {_formatar(ag.inicio)}")
        return f"Consulta {ag.pk} remarcada para {_formatar(ag.inicio)}. Status: aguardando confirmação."

    if nome == "cancelar_consulta":
        ag = agendamentos.cancelar(
            _consulta_do_paciente(conversa, entrada["consulta_id"]),
            motivo=f"Pelo paciente (WhatsApp): {entrada.get('motivo', '')}"[:255],
        )
        resultado.acoes.append(f"cancelou consulta {ag.pk}")
        return f"Consulta {ag.pk} cancelada."

    if nome == "salvar_nome_paciente":
        paciente = conversa.paciente
        paciente.nome = entrada["nome"].strip()[:150]
        paciente.save(update_fields=["nome"])
        resultado.acoes.append(f"salvou o nome “{paciente.nome}”")
        return f"Nome salvo: {paciente.nome}."

    if nome == "transferir_para_humano":
        from .services import transferir_para_humano  # import tardio: services importa este módulo

        transferir_para_humano(conversa, entrada.get("motivo", ""), entrada.get("resumo", ""))
        resultado.transferiu = True
        resultado.acoes.append(f"transferiu para a equipe ({entrada.get('motivo', '')})")
        return "Conversa transferida para a equipe. Avise o paciente que alguém vai continuar o atendimento por aqui."

    raise ErroAssistente(f"Ferramenta desconhecida: {nome}")


# ---------------------------------------------------------------------------
# Histórico e chamada à API
# ---------------------------------------------------------------------------


def montar_historico(conversa, agora=None):
    """Mensagens recentes como turnos user/assistant (só texto). Começa sempre por mensagem do paciente."""
    agora = agora or timezone.now()
    recentes = list(
        conversa.mensagens.filter(criada_em__gte=agora - JANELA_HISTORICO)
        .exclude(origem=Mensagem.Origem.NOTA)  # nota interna é só da equipe — nunca vai para a IA
        .order_by("-criada_em")[:MAX_MENSAGENS_HISTORICO]
    )[::-1]
    while recentes and recentes[0].origem != Mensagem.Origem.PACIENTE:
        recentes.pop(0)
    historico = []
    for m in recentes:
        if m.origem == Mensagem.Origem.PACIENTE:
            papel, texto = "user", m.texto
        elif m.origem == Mensagem.Origem.HUMANO:
            papel, texto = "assistant", f"[Mensagem enviada pela equipe da clínica]: {m.texto}"
        else:
            papel, texto = "assistant", m.texto
        if historico and historico[-1]["role"] == papel:
            historico[-1]["content"] += f"\n{texto}"
        else:
            historico.append({"role": papel, "content": texto})
    return historico



def _executar_com_tratamento(conversa, nome, entrada, resultado):
    """Executa a ferramenta. Devolve (texto, deu_erro) — erros viram texto para o modelo corrigir."""
    try:
        return executar_ferramenta(conversa, nome, entrada, resultado), False
    except (ErroAssistente, agendamentos.ErroAgendamento) as e:
        return str(e), True
    except KeyError as e:
        return f"Faltou o campo obrigatório {e} na chamada de {nome}.", True
    except Exception as e:  # erro inesperado: registra e deixa o modelo lidar
        logger.exception("Erro na ferramenta %s (conversa %s)", nome, conversa.pk)
        return f"Erro interno ao executar a ferramenta: {type(e).__name__}", True


def _transferir_por_recusa(conversa, resultado, detalhe):
    from .services import transferir_para_humano  # import tardio: services importa este módulo

    logger.warning("IA recusou responder na conversa %s (%s). Transferindo.", conversa.pk, detalhe)
    transferir_para_humano(conversa, "Assistente não pôde responder (recusa de segurança)", "")
    resultado.transferiu = True
    return resultado  # sem texto: quem chama envia a mensagem padrão de transferência


def _registrar_uso(conversa, resultado, entrada, saida, cache=0):
    logger.info(
        "Assistente respondeu (conversa %s): %d tokens de entrada (%d do cache), %d de saída. Ações: %s",
        conversa.pk, entrada or 0, cache or 0, saida or 0, "; ".join(resultado.acoes) or "nenhuma",
    )


# --- Anthropic (Claude) ------------------------------------------------------


def _turno_anthropic(conversa, config, prompt_fixo, prompt_turno, mensagens, resultado, cliente):
    system = [
        {"type": "text", "text": prompt_fixo, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": prompt_turno},
    ]
    for _ in range(MAX_ITERACOES):
        resposta = cliente.beta.messages.create(
            model=config.ia_modelo,
            max_tokens=16000,
            system=system,
            tools=FERRAMENTAS,
            messages=mensagens,
            output_config={"effort": ESFORCO},
            betas=BETAS,
            fallbacks="default",
        )
        if resposta.stop_reason == "refusal":
            categoria = getattr(resposta.stop_details, "category", None) if resposta.stop_details else None
            return _transferir_por_recusa(conversa, resultado, f"categoria: {categoria}")

        # Turno ainda em andamento (servidor pausou): devolve o conteúdo e continua.
        if resposta.stop_reason == "pause_turn":
            mensagens.append({"role": "assistant", "content": resposta.content})
            continue

        chamadas = [b for b in resposta.content if b.type == "tool_use"]
        if resposta.stop_reason == "tool_use" and chamadas:
            mensagens.append({"role": "assistant", "content": resposta.content})
            retornos = []
            for chamada in chamadas:
                texto, erro = _executar_com_tratamento(conversa, chamada.name, chamada.input, resultado)
                retorno = {"type": "tool_result", "tool_use_id": chamada.id, "content": texto}
                if erro:
                    retorno["is_error"] = True
                retornos.append(retorno)
            mensagens.append({"role": "user", "content": retornos})
            continue

        resultado.texto = "\n\n".join(b.text for b in resposta.content if b.type == "text").strip()
        if resposta.stop_reason == "max_tokens":
            logger.warning("Resposta da IA cortada por max_tokens (conversa %s).", conversa.pk)
        uso = resposta.usage
        _registrar_uso(conversa, resultado, uso.input_tokens, uso.output_tokens, uso.cache_read_input_tokens)
        return resultado
    raise ErroAssistente(f"O assistente passou de {MAX_ITERACOES} etapas sem concluir a resposta.")


# --- Gemini e outros compatíveis com OpenAI --------------------------------

# Gemini: "low" basta para atendimento por chat e economiza a cota do plano gratuito.
ESFORCO_GEMINI = "low"


def ferramentas_compativeis():
    """FERRAMENTAS no formato OpenAI, só com o subconjunto de JSON Schema que o Gemini aceita.

    - sem `strict`/`additionalProperties`;
    - campo que aceita null vira opcional (fora de `required`);
    - ferramenta sem parâmetros vai sem `parameters` (o Gemini recusa objeto sem propriedades).
    """
    convertidas = []
    for f in FERRAMENTAS:
        esquema = copy.deepcopy(f["input_schema"])
        esquema.pop("additionalProperties", None)
        obrigatorias = list(esquema.get("required", []))
        for nome, prop in esquema["properties"].items():
            if isinstance(prop.get("type"), list):
                prop["type"] = next(t for t in prop["type"] if t != "null")
                prop["description"] = (prop.get("description", "") + " Opcional: omita para não filtrar.").strip()
                if nome in obrigatorias:
                    obrigatorias.remove(nome)
        esquema["required"] = obrigatorias
        funcao = {"name": f["name"], "description": f["description"]}
        if esquema["properties"]:
            funcao["parameters"] = esquema
        convertidas.append({"type": "function", "function": funcao})
    return convertidas


def _turno_compativel(conversa, config, prompt_fixo, prompt_turno, historico, resultado, cliente):
    mensagens = [{"role": "system", "content": f"{prompt_fixo}\n\n{prompt_turno}"}, *historico]
    ferramentas = ferramentas_compativeis()
    extras = {"reasoning_effort": ESFORCO_GEMINI} if config.ia_provedor == Configuracao.Provedor.GEMINI else {}
    entrada_total = saida_total = 0
    modelo = config.ia_modelo
    for iteracao in range(MAX_ITERACOES):
        try:
            resposta = cliente.chat.completions.create(
                model=modelo, messages=mensagens, tools=ferramentas, tool_choice="auto", **extras,
            )
        except (openai.InternalServerError, openai.RateLimitError) as e:
            # Sobrecarga (503) ou cota (429) mesmo depois das tentativas do SDK: tenta o modelo reserva.
            # Só na 1ª chamada do turno — depois disso o histórico já tem thought_signatures do modelo
            # principal, que o reserva recusaria (400).
            reserva = ia.modelo_reserva(config)
            if iteracao > 0 or not reserva or modelo == reserva:
                raise
            logger.warning("Modelo %s indisponível (%s); usando o reserva %s nesta resposta.",
                           modelo, ia.descrever_erro(e), reserva)
            modelo = reserva
            resposta = cliente.chat.completions.create(
                model=modelo, messages=mensagens, tools=ferramentas, tool_choice="auto", **extras,
            )
        if resposta.usage:
            entrada_total += resposta.usage.prompt_tokens or 0
            saida_total += resposta.usage.completion_tokens or 0
        escolha = resposta.choices[0]
        mensagem = escolha.message
        if escolha.finish_reason == "content_filter":
            return _transferir_por_recusa(conversa, resultado, "filtro de conteúdo do provedor")

        if mensagem.tool_calls:
            # Devolve a mensagem INTEIRA: no Gemini cada tool_call traz
            # extra_content.google.thought_signature, que precisa voltar igual (senão: erro 400).
            mensagens.append(mensagem.model_dump(exclude_none=True))
            for chamada in mensagem.tool_calls:
                try:
                    entrada = json.loads(chamada.function.arguments or "{}")
                    texto, erro = _executar_com_tratamento(conversa, chamada.function.name, entrada, resultado)
                except json.JSONDecodeError:
                    texto, erro = "Argumentos em JSON inválido; chame a ferramenta de novo.", True
                mensagens.append({
                    "role": "tool", "tool_call_id": chamada.id,
                    "content": f"ERRO: {texto}" if erro else texto,
                })
            continue

        resultado.texto = (mensagem.content or "").strip()
        if escolha.finish_reason == "length":
            logger.warning("Resposta da IA cortada por limite de tamanho (conversa %s).", conversa.pk)
        _registrar_uso(conversa, resultado, entrada_total, saida_total)
        return resultado
    raise ErroAssistente(f"O assistente passou de {MAX_ITERACOES} etapas sem concluir a resposta.")


# --- Entrada -----------------------------------------------------------------


def _cliente(config):
    if not config.ia_api_key:
        raise ErroAssistente("API key da IA não configurada.")
    if config.ia_provedor == Configuracao.Provedor.ANTHROPIC:
        return anthropic.Anthropic(api_key=config.ia_api_key, timeout=90.0, max_retries=2)
    if config.ia_provedor == Configuracao.Provedor.COMPATIVEL and not config.ia_base_url.strip():
        raise ErroAssistente("Informe a URL da API do provedor em Integrações.")
    return ia.cliente_compativel(config)


def responder(conversa, agora=None, cliente=None):
    """Gera a resposta do assistente para a conversa. Não envia nada — quem chama envia."""
    config = Configuracao.obter()
    agora = agora or timezone.now()
    resultado = Resultado()
    historico = montar_historico(conversa, agora)
    if not historico or historico[-1]["role"] != "user":
        return resultado  # nada novo do paciente para responder

    cliente = cliente or _cliente(config)
    prompt_fixo = _prompt_fixo(config, conversa.unidade.clinica)
    prompt_turno = _prompt_do_turno(conversa, agora)
    if config.ia_provedor == Configuracao.Provedor.ANTHROPIC:
        return _turno_anthropic(conversa, config, prompt_fixo, prompt_turno, historico, resultado, cliente)
    return _turno_compativel(conversa, config, prompt_fixo, prompt_turno, historico, resultado, cliente)


def descrever_erro_api(e):
    """Mensagem curta e útil para o log a partir de um erro de qualquer SDK de IA."""
    return ia.descrever_erro(e)
