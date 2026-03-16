from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from supabase import create_client, Client
import asyncio


app = FastAPI()

# Permite que o site visual converse com o servidor
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- CONFIGURAÇÕES ----------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://brbjlpcpiubtlneualrv.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") 
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------- MODELOS ----------------
class Aposta(BaseModel):
    nome: str
    whatsapp: str
    palpites: Dict[str, str]

class NovoJogo(BaseModel):
    apiId: Optional[int] = None
    home: str
    away: str
    championship: str
    datetime: str

class Configuracoes(BaseModel):
    pix_key: str
    pix_name: str
    pix_amount: str
    whatsapp: str
    prize_1: str
    prize_2: str
    prize_3: str

# ---------------- SEGURANÇA ----------------
def verificar_admin(x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Acesso negado.")


def calcular_ranking(jogos_oficiais, todas_apostas):
    ranking = []
    jogos_por_id = {str(j.get("id")): j for j in jogos_oficiais}

    for aposta in todas_apostas:
        pontos = 0
        palpites = aposta.get("palpites", {})
        jogos_realizados = []

        for jogo in jogos_oficiais:
            jogo_id = str(jogo["id"])
            resultado_real = jogo.get("result") # Ex: {"home": "2", "away": "1"}

            try:
                meu_palpite = palpites.get(jogo_id)
                if meu_palpite:
                    jogos_realizados.append({
                        "jogo_id": jogo_id,
                        "home": jogo.get("home"),
                        "away": jogo.get("away"),
                        "palpite": meu_palpite
                    })

                # Pula o jogo se ele ainda não tiver resultado oficial
                if not resultado_real or resultado_real.get("home") is None or resultado_real.get("home") == "":
                    continue

                # 1. Descobre quem ganhou na vida real
                gols_h = int(resultado_real["home"])
                gols_a = int(resultado_real["away"])
                vencedor_real = "home" if gols_h > gols_a else "away" if gols_a > gols_h else "draw"

                # 2. Compara com o palpite do usuário para esse jogo específico
                if meu_palpite == vencedor_real:
                    pontos += 1
            except Exception as e:
                print(f"Erro ao processar pontos do jogo {jogo_id}: {e}")
                continue

        # Inclui também palpites de jogos que não estão na lista oficial atual
        for jogo_id, palpite in (palpites or {}).items():
            if jogo_id in jogos_por_id:
                continue
            jogos_realizados.append({
                "jogo_id": jogo_id,
                "home": f"Jogo #{jogo_id}",
                "away": "(não encontrado)",
                "palpite": palpite
            })

        # Adiciona o resumo desse apostador na lista
        ranking.append({
            "nome": aposta.get("nome", "Anônimo"),
            "pontos": pontos,
            "whatsapp": aposta.get("whatsapp", ""),
            "jogos_realizados": jogos_realizados
        })

    # Ordena: quem tem mais pontos fica no topo (🥇)
    return sorted(ranking, key=lambda x: x["pontos"], reverse=True)

# ---------------- ROTAS DO SITE ----------------

@app.get("/")
async def home():
    return FileResponse("index.html")

@app.get("/jogos")
async def listar_jogos():
    res = supabase.table("jogos").select("*").order("datetime").execute()
    return res.data

@app.get("/buscar-api")
async def buscar_api(data: str = None):
    """Puxa jogos da internet usando sua chave secreta"""
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")
    url = f"https://v3.football.api-sports.io/fixtures?date={data}&timezone=America/Sao_Paulo"
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        return r.json()

@app.post("/salvar-aposta")
async def salvar_aposta(aposta: Aposta):
    # Puxar todos os jogos ativos para verificar o horário
    res = supabase.table("jogos").select("datetime").eq("active", True).execute()
    jogos = res.data
    
    if not jogos:
        raise HTTPException(status_code=400, detail="Não existem jogos ativos no bolão.")

    # Encontrar o horário do primeiro jogo
    # Nota: A API costuma enviar em formato ISO (ex: 2024-03-12T21:30:00+00:00)
    try:
        horarios = []
        for j in jogos:
            dt = datetime.fromisoformat(j['datetime'].replace('Z', '+00:00'))
            horarios.append(dt)
        
        primeiro_jogo = min(horarios)
        agora = datetime.now(timezone.utc)

        # Se o jogo já começou ou faltam menos de 1 minuto
        if agora > primeiro_jogo:
            raise HTTPException(
                status_code=403, 
                detail="As apostas estão encerradas! O primeiro jogo já começou."
            )
            
    except Exception as e:
        print(f"Erro ao validar horário: {e}")
        # Se houver erro na data, deixamos passar por segurança ou bloqueamos? 
        # Idealmente bloqueamos se não conseguirmos validar.

    # Se passou na validação, guarda a aposta
    try:
        supabase.table("apostas").insert(aposta.model_dump()).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ranking-geral")
async def obter_ranking_geral():
    """Gera o ranking atualizado apenas com apostas PAGAS"""
    try:
        # Puxa todos os jogos para conferir os resultados
        jogos = supabase.table("jogos").select("*").execute().data
        
        # 🎯 O PULO DO GATO: Filtra apenas quem tem 'pago' como True
        apostas_pagas = supabase.table("apostas").select("*").eq("pago", True).execute().data
        
        if not apostas_pagas:
            return []
            
        # Chama a função de cálculo que criamos acima
        return calcular_ranking(jogos, apostas_pagas)
        
    except Exception as e:
        print(f"Erro ao gerar ranking: {e}")
        return []
# ---------------- ROTAS ADMIN ----------------

@app.post("/login-admin")
async def login(req: Dict):
    if req.get("senha") == ADMIN_PASSWORD:
        return {"status": "sucesso", "token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401)

@app.get("/obter-api-key", dependencies=[Depends(verificar_admin)])
async def get_key():
    return {"api_key": FOOTBALL_API_KEY}

@app.post("/adicionar-jogo", dependencies=[Depends(verificar_admin)])
async def add_jogo(jogo: NovoJogo):
    supabase.table("jogos").insert(jogo.model_dump()).execute()
    return {"status": "sucesso"}

@app.delete("/remover-jogo/{jogo_id}", dependencies=[Depends(verificar_admin)])
async def remover_jogo(jogo_id: str):
    supabase.table("jogos").delete().eq("id", jogo_id).execute()
    return {"status": "sucesso"}

@app.put("/alternar-status-jogo/{jogo_id}", dependencies=[Depends(verificar_admin)])
async def status_jogo(jogo_id: str, ativo: bool):
    supabase.table("jogos").update({"active": ativo}).eq("id", jogo_id).execute()
    return {"status": "sucesso"}

@app.get("/configuracoes")
async def get_config():
    res = supabase.table("configuracoes").select("*").eq("id", 1).execute()
    return res.data[0] if res.data else {}

@app.post("/salvar-configuracoes", dependencies=[Depends(verificar_admin)])
async def save_config(config: Configuracoes):
    supabase.table("configuracoes").update(config.model_dump()).eq("id", 1).execute()
    return {"status": "sucesso"}

# ---------------- AUTOMAÇÃO ----------------
@app.on_event("startup")
async def startup():
    # Inicia a tarefa em segundo plano assim que o servidor liga
    asyncio.create_task(sincronizar_resultados())

async def sincronizar_resultados():
    while True:
        try:
            print(f"[{datetime.now()}] Iniciando verificação de resultados...")
            
            # 1. Busca jogos que ainda não têm resultado (result is null)
            res = supabase.table("jogos").select("id, apiId, home, away").filter("result", "is", "null").execute()
            jogos_pendentes = res.data
            
            if jogos_pendentes:
                print(f"Encontrados {len(jogos_pendentes)} jogos para atualizar.")
                
                async with httpx.AsyncClient() as client:
                    headers = {"x-apisports-key": FOOTBALL_API_KEY}
                    
                    for j in jogos_pendentes:
                        api_id = j.get("apiId")
                        if not api_id:
                            continue
                        
                        print(f"Checando: {j['home']} x {j['away']} (ID API: {api_id})")
                        
                        # 2. Faz a chamada para a API
                        response = await client.get(
                            f"https://v3.football.api-sports.io/fixtures?id={api_id}", 
                            headers=headers,
                            timeout=10.0
                        )
                        
                        d = response.json()
                        
                        # Verifica se a API retornou erro de limite/suspensão
                        if d.get("errors"):
                            print(f"⚠️ AVISO DA API: {d['errors']}")
                            break # Para o loop para não piorar a situação

                        if d.get("response") and len(d["response"]) > 0:
                            fix = d["response"][0]
                            status = fix["fixture"]["status"]["short"]
                            
                            # Se o jogo terminou (FT, AET ou PEN)
                            if status in ["FT", "AET", "PEN"]:
                                g = fix["goals"]
                                home_goals = str(g["home"]) if g["home"] is not None else "0"
                                away_goals = str(g["away"]) if g["away"] is not None else "0"
                                
                                # 3. Atualiza o banco de dados
                                supabase.table("jogos").update({
                                    "result": {"home": home_goals, "away": away_goals}
                                }).eq("id", j["id"]).execute()
                                
                                print(f"✅ Resultado salvo: {j['home']} {home_goals} x {away_goals} {j['away']}")
                        
                        # ✨ O SEGREDO ANTI-BANIMENTO ESTÁ AQUI:
                        # Espera 12 segundos antes de consultar o próximo jogo da lista.
                        # Isso evita o gatilho de "tiro rápido" que suspende sua conta.
                        await asyncio.sleep(12)
            
            else:
                print("Nenhum jogo pendente encontrado.")

        except Exception as e:
            print(f"❌ Erro no loop de sincronização: {e}")
        
        # Espera 1 hora (3600 segundos) para rodar a lista toda novamente
        print(f"[{datetime.now()}] Ciclo finalizado. Próxima checagem em 1 hora.")
        await asyncio.sleep(3600)


@app.get("/admin/apostas", dependencies=[Depends(verificar_admin)])
async def listar_apostas_admin():
    """Lista TODAS as apostas para o admin conferir e aprovar"""
    res = supabase.table("apostas").select("*").order("created_at", desc=True).execute()
    return res.data

@app.put("/admin/aprovar-pagamento/{aposta_id}", dependencies=[Depends(verificar_admin)])
async def aprovar_pagamento(aposta_id: str, status: bool):
    """Muda o status de pagamento de uma aposta"""
    try:
        supabase.table("apostas").update({"pago": status}).eq("id", aposta_id).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/admin/zerar-apostas", dependencies=[Depends(verificar_admin)])
async def zerar_apostas():
    """Remove TODAS as apostas do banco de dados para iniciar um novo ciclo"""
    try:
        # Comando para deletar todas as linhas da tabela 'apostas'
        supabase.table("apostas").delete().neq("nome", "FORCAR_DELETE_TOTAL").execute()
        return {"status": "sucesso"}
    except Exception as e:
        print(f"Erro ao zerar: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/sincronizar-manual", dependencies=[Depends(verificar_admin)])
async def sincronizar_manual():
    """Dispara a atualização de resultados imediatamente (em background)"""
    asyncio.create_task(sincronizar_resultados_processo_unico())
    return {"status": "Sincronização iniciada em segundo plano"}

@app.get("/admin/relatorio-jogos", dependencies=[Depends(verificar_admin)])
async def relatorio_jogos_admin(ultimos_dias: Optional[int] = None):
    """Gera um relatório de apostas separadas por dia para uso administrativo."""
    try:
        apostas = supabase.table("apostas").select("id, nome, whatsapp, pago, palpites, created_at").order("created_at", desc=True).execute().data

        if ultimos_dias is not None:
            if ultimos_dias <= 0:
                raise HTTPException(status_code=400, detail="ultimos_dias deve ser maior que zero")

            limite = datetime.now(timezone.utc) - timedelta(days=ultimos_dias)
            apostas_filtradas = []
            for aposta in apostas:
                dt_raw = aposta.get("created_at")
                if not dt_raw:
                    continue
                try:
                    dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt >= limite:
                    apostas_filtradas.append(aposta)
            apostas = apostas_filtradas

        total_apostas = len(apostas)
        total_apostas_pagas = len([a for a in apostas if a.get("pago")])

        return JSONResponse({
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "filtro": {"ultimos_dias": ultimos_dias},
            "resumo": {
                "total_apostas": total_apostas,
                "total_apostas_pagas": total_apostas_pagas,
                "total_apostas_pendentes": total_apostas - total_apostas_pagas
            },
            "apostas": apostas
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def sincronizar_resultados_processo_unico():
    """Versão da função de sync que corre apenas uma vez (sem loop infinito)"""
    try:
        res = supabase.table("jogos").select("id, apiId, home, away").filter("result", "is", "null").execute()
        jogos_pendentes = res.data
        if jogos_pendentes:
            async with httpx.AsyncClient() as client:
                headers = {"x-apisports-key": FOOTBALL_API_KEY}
                for j in jogos_pendentes:
                    api_id = j.get("apiId")
                    if not api_id: continue
                    response = await client.get(f"https://v3.football.api-sports.io/fixtures?id={api_id}", headers=headers)
                    d = response.json()
                    if d.get("response") and len(d["response"]) > 0:
                        fix = d["response"][0]
                        if fix["fixture"]["status"]["short"] in ["FT", "AET", "PEN"]:
                            g = fix["goals"]
                            supabase.table("jogos").update({
                                "result": {"home": str(g["home"]), "away": str(g["away"])}
                            }).eq("id", j["id"]).execute()
                    await asyncio.sleep(12) # Segurança anti-ban
    except Exception as e:
        print(f"Erro no sync manual: {e}")
